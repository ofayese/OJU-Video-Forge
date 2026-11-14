"""Narration stages on the existing durable job/outbox and artifact model."""

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path

import httpx
from sqlalchemy import select

from . import narration as media
from .agents import AgentUnavailable, get_agent_adapter
from .core import (
    ARTIFACT_ROOT,
    Audit,
    MediaAsset,
    Narration,
    NarrationRevision,
    ProjectMembership,
    User,
    WorkflowArtifact,
)
from .private_storage import private_file, write_private_file
from .training import (
    draft_digest,
    draft_matches_script,
    draft_script,
    training_markdown,
    validate_procedure,
    validate_script_evidence,
    validate_training_draft,
)


def stage(db, row, name, check):
    check()
    row.stage = name
    row.error = None
    db.add(Audit(job_id=row.job_id, actor="worker", action=f"narration:{name}"))
    db.commit()


def register(db, row, job, filename, content, media_type):
    artifact_id = str(uuid.uuid4())
    key = f"{job.id}/{artifact_id}/{filename}"
    write_private_file(ARTIFACT_ROOT, key, content)
    path = private_file(ARTIFACT_ROOT, key)
    db.add(WorkflowArtifact(
        id=artifact_id, job_id=job.id, project_id=row.project_id, owner=job.owner,
        relative_path=key, filename=filename, media_type=media_type,
        sha256=media.digest(path), size_bytes=path.stat().st_size,
    ))
    return artifact_id, key


def execute(db, job, check):
    row = db.get(Narration, job.spec["narration_id"])
    if not row or row.job_id != job.id or row.project_id != job.project_id:
        raise media.NarrationError("narration_job_identity_mismatch")
    asset = db.get(MediaAsset, row.media_id)
    if not asset or asset.project_id != row.project_id:
        raise media.NarrationError("source_media_unavailable")
    source = private_file(ARTIFACT_ROOT, asset.storage_key)
    if (source.stat().st_size != asset.size_bytes or asset.sha256 != row.source_sha256
            or media.digest(source) != row.source_sha256):
        raise media.NarrationError("source_media_integrity_changed")
    options = media.NarrationOptions.model_validate(row.options)
    if asset.size_bytes > 512 * 1024 * 1024:
        raise media.NarrationError("source_size_limit")
    with tempfile.TemporaryDirectory(prefix="narration-") as work:
        directory = Path(work)
        metadata = media.probe(source, check)
        if not any(item["codec_type"] == "video" for item in metadata["streams"]):
            raise media.NarrationError("source_video_required")
        if job.spec["phase"] == "voice_preview":
            stage(db, row, "synthesizing_audio", check)
            sample = [{"id": "sample", "start_ms": 0, "end_ms": 10000,
                       "text": "This is a local narration voice preview."}]
            audio = media.synthesize(sample, directory, options, 10, check)
            register(db, row, job, "voice-preview.wav", audio.read_bytes(), "audio/wav")
            row.stage = job.spec["return_stage"]
            db.commit()
            return None
        if job.spec["phase"] == "analysis":
            stage(db, row, "transcribing", check)
            transcript = (
                media.transcribe(source, directory, metadata, check)
                if options.workflow != "silent_training" and options.analysis_mode == "transcript"
                else {"status": "manual_not_transcribed", "segments": [], "language": None}
            )
            stage(db, row, "analyzing_scenes", check)
            scene_error = None
            try:
                observations = media.scenes(source, directory, metadata, options, check)
            except (media.NarrationError, httpx.HTTPError, json.JSONDecodeError) as exc:
                observations = []
                scene_error = (
                    (str(exc) or "optional_visual_analysis_failed")
                    if isinstance(exc, media.NarrationError)
                    else "visual_provider_request_or_response_failed"
                )
                db.add(Audit(job_id=job.id, actor="worker",
                             action="narration:optional_scene_analysis_unavailable"))
            provider_error = any(
                item.get("ocr_status") == "unavailable"
                or item.get("vision_status") == "unavailable"
                for item in observations
            )
            if provider_error:
                db.add(Audit(job_id=job.id, actor="worker",
                             action="narration:optional_scene_provider_unavailable"))
            stage(db, row, "drafting_script", check)
            row.analysis = {
                "media": metadata, "transcript": transcript, "scenes": observations,
                "scene_analysis": {
                    "status": (
                        "unavailable" if scene_error
                        else "partial" if provider_error
                        else "complete"
                    ),
                    "error": scene_error or (
                        "one_or_more_optional_providers_failed" if provider_error else None
                    ),
                },
                "ocr": (
                    "unavailable_optional_scene_analysis" if scene_error
                    else "partial" if any(item.get("ocr_status") == "unavailable" for item in observations)
                    else "complete" if options.ocr
                    else "unavailable_not_requested"
                ),
                "vision": (
                    "unavailable_optional_scene_analysis" if scene_error
                    else "partial_local_model_observations_require_review"
                    if options.vision and any(item.get("vision_status") == "unavailable" for item in observations)
                    else "local_model_observations_require_review" if options.vision
                    else "unavailable_not_requested"
                ),
                "style": options.style, "instructions": options.instructions,
                "language": options.language,
            }
            row.revision += 1
            row.approved_revision = None
            if options.workflow == "silent_training":
                if not observations:
                    raise media.NarrationError("training_scene_evidence_unavailable")
                for frame in observations:
                    check()
                    frame_id = frame["id"]
                    if not frame_id.startswith("frame-") or not frame_id[6:].isdigit():
                        raise media.NarrationError("training_scene_identity_invalid")
                    frame_path = directory / f"{frame_id}.jpg"
                    if (frame_path.is_symlink() or not frame_path.is_file()
                            or frame_path.stat().st_size > media.OCR_MAX_IMAGE_BYTES
                            or media.digest(frame_path) != frame.get("frame_sha256")):
                        raise media.NarrationError("training_scene_integrity_invalid")
                    artifact_id, _ = register(
                        db, row, job, f"{frame_id}.jpg", frame_path.read_bytes(), "image/jpeg",
                    )
                    frame["artifact_id"] = artifact_id
                duration = round(float(metadata["format"]["duration"]) * 1000)
                steps = [
                    {
                        "id": f"step-{index + 1}",
                        "start_ms": frame["timestamp_ms"],
                        "end_ms": observations[index + 1]["timestamp_ms"]
                        if index + 1 < len(observations) else duration,
                        "description": "",
                        "evidence": [frame["id"]],
                        "uncertainty": ["click", "keyboard", "intent", "visual"],
                        "decision": "pending",
                    }
                    for index, frame in enumerate(observations)
                ]
                row.analysis = {
                    **row.analysis,
                    "procedure": {
                        "contract_version": 1,
                        "evidence_version": row.revision,
                        "status": "pending",
                        "review_history": [],
                        "steps": validate_procedure(
                            steps, {frame["id"] for frame in observations}, duration
                        ),
                    },
                }
                script = []
            else:
                script = media.draft(row.analysis, options)
            db.add(NarrationRevision(
                id=str(uuid.uuid4()), narration_id=row.id, revision=row.revision,
                editor_id=job.owner_principal_id, script=script,
            ))
            register(db, row, job, "analysis.json",
                     json.dumps(row.analysis, ensure_ascii=False).encode(), "application/json")
            text = "\n".join(item["text"] for item in transcript["segments"])
            register(db, row, job, "transcript.txt", text.encode(), "text/plain")
            for fmt, kind in (("srt", "application/x-subrip"), ("vtt", "text/vtt")):
                register(db, row, job, f"transcript.{fmt}", media.subtitles(transcript["segments"], fmt), kind)
            row.stage = (
                "awaiting_procedure_review" if options.workflow == "silent_training"
                else "awaiting_script_approval"
            )
            db.commit()
            return None

        if job.spec["phase"] == "training_draft":
            procedure = row.analysis.get("procedure", {})
            approver = db.get(User, procedure.get("approved_by"))
            role = db.scalar(select(ProjectMembership.role).where(
                ProjectMembership.project_id == row.project_id,
                ProjectMembership.user_id == procedure.get("approved_by"),
            ))
            if (options.workflow != "silent_training" or procedure.get("status") != "approved"
                    or not approver or approver.disabled or role not in {"owner", "editor", "reviewer"}
                    or job.spec["revision"] != row.revision):
                raise media.NarrationError("approved_procedure_required")
            duration = round(float(metadata["format"]["duration"]) * 1000)
            steps = validate_procedure(
                procedure["steps"], {frame["id"] for frame in row.analysis["scenes"]},
                duration, approved=True,
            )
            accepted = [item for item in steps if item["decision"] == "accepted"]
            allowed = {evidence for item in accepted for evidence in item["evidence"]}
            evidence = {
                "contract_version": 1, "source_sha256": row.source_sha256,
                "duration_ms": duration, "approved_steps": accepted,
                "frames": [
                    {"id": frame["id"], "timestamp_ms": frame["timestamp_ms"],
                     "ocr": (frame.get("ocr") or "")[:512]}
                    for frame in row.analysis["scenes"] if frame["id"] in allowed
                ],
                "request": job.spec["training"],
            }
            stage(db, row, "drafting_script", check)
            try:
                adapter = get_agent_adapter()
                draft = validate_training_draft(
                    adapter.generate_training(evidence, check), allowed, duration, accepted
                )
                script = media.validate_script(draft_script(draft), duration)
            except AgentUnavailable as exc:
                raise media.NarrationError(str(exc)) from exc
            except ValueError as exc:
                raise media.NarrationError("invalid_training_model_output") from exc
            row.analysis = {
                **row.analysis,
                "training_module": {
                    "contract_version": 1, "draft": draft.model_dump(),
                    "provider": adapter.provenance(),
                },
            }
            row.revision += 1
            row.approved_revision = None
            db.add(NarrationRevision(
                id=str(uuid.uuid4()), narration_id=row.id, revision=row.revision,
                editor_id=job.owner_principal_id, script=script,
            ))
            row.stage = "awaiting_script_approval"
            db.commit()
            return None

        revision = db.scalar(select(NarrationRevision).where(
            NarrationRevision.narration_id == row.id,
            NarrationRevision.revision == row.revision,
        ))
        approver = db.get(User, revision.approved_by) if revision and revision.approved_by else None
        role = db.scalar(select(ProjectMembership.role).where(
            ProjectMembership.project_id == row.project_id,
            ProjectMembership.user_id == revision.approved_by if revision else False,
        ))
        if (row.approved_revision != row.revision or not revision or not approver
                or approver.disabled
                or role not in ({"owner", "editor", "reviewer"} if options.workflow == "silent_training"
                                else {"owner", "editor"})
                or job.spec["revision"] != row.revision):
            raise media.NarrationError("approved_script_revision_required")
        script = media.validate_script(revision.script, round(float(metadata["format"]["duration"]) * 1000))
        if options.workflow == "silent_training":
            procedure = row.analysis.get("procedure", {})
            procedure_approver = db.get(User, procedure.get("approved_by"))
            procedure_role = db.scalar(select(ProjectMembership.role).where(
                ProjectMembership.project_id == row.project_id,
                ProjectMembership.user_id == procedure.get("approved_by"),
            ))
            if (procedure.get("status") != "approved" or not procedure_approver
                    or procedure_approver.disabled
                    or procedure_role not in {"owner", "editor", "reviewer"}):
                raise media.NarrationError("approved_procedure_required")
            steps = validate_procedure(
                procedure["steps"], {frame["id"] for frame in row.analysis["scenes"]},
                round(float(metadata["format"]["duration"]) * 1000), approved=True,
            )
            try:
                validate_script_evidence(script, steps)
                module = row.analysis.get("training_module")
                if not module:
                    raise ValueError("training_draft_required")
                allowed = {ref for step in steps if step["decision"] == "accepted" for ref in step["evidence"]}
                draft = validate_training_draft(
                    module["draft"], allowed, round(float(metadata["format"]["duration"]) * 1000), steps,
                )
                if (module.get("approved_sha256") != draft_digest(draft.model_dump())
                        or not draft_matches_script(draft, script)):
                    raise ValueError("training_draft_approval_changed")
            except ValueError as exc:
                raise media.NarrationError("training_approval_or_evidence_invalid") from exc
        audio = None
        if options.audio_mode != "preserve":
            stage(db, row, "synthesizing_audio", check)
            audio = media.synthesize(script, directory, options, float(metadata["format"]["duration"]), check)
        stage(db, row, "mixing_audio", check)
        output = directory / "video.mp4"
        if options.render_annotations and options.workflow != "silent_training":
            raise media.NarrationError("reviewed_training_annotations_required")
        annotations = [item.model_dump() for item in draft.annotations] if options.render_annotations else None
        result = media.mux(source, audio, output, metadata, options, check, annotations)
        stage(db, row, "verifying_artifact", check)
        if output.stat().st_size > 256 * 1024 * 1024 or media.digest(source) != row.source_sha256:
            raise media.NarrationError("derived_quota_or_source_integrity_failure")
        provenance = {
            "schema_version": 1, "operation": "narrate_existing_video",
            "project_id": row.project_id, "job_id": job.id, "source_media_id": row.media_id,
            "source_sha256": row.source_sha256, "script_revision": row.revision,
            "script_sha256": hashlib.sha256(
                json.dumps(script, sort_keys=True).encode()).hexdigest(),
            "approved_by": revision.approved_by, "options": options.model_dump(),
            "transcription": row.analysis["transcript"].get("provider", "not_run"),
            "tts": {
                "provider": "espeak-ng" if audio else "not_run_original_audio_preserved",
                "version": media.run(["espeak-ng", "--version"], check).decode().splitlines()[0] if audio else None,
                "license": "GPL-3.0-or-later",
            },
            "ffmpeg_version": media.run(["ffmpeg", "-version"], check).decode().splitlines()[0],
            "ffprobe": result, "source_media": metadata,
            "visual_stream_sha256": media.video_hash(output, check),
            "visual_transform": "reviewed_annotations" if options.render_annotations else "none",
            "output_sha256": media.digest(output), "output_bytes": output.stat().st_size,
        }
        video_id, video_key = register(db, row, job, "video.mp4", output.read_bytes(), "video/mp4")
        if options.workflow == "silent_training":
            register(db, row, job, "procedure.json",
                     json.dumps(row.analysis["procedure"], ensure_ascii=False).encode(), "application/json")
            register(db, row, job, "teaching-script.json",
                     json.dumps(script, ensure_ascii=False).encode(), "application/json")
            manifest = {
                "contract_version": 1, "project_id": row.project_id,
                "source_media_id": row.media_id, "source_sha256": row.source_sha256,
                "procedure": procedure, "frames": row.analysis["scenes"],
                "script_revision": row.revision, "script_approved_by": revision.approved_by,
                "script_approved_at": revision.approved_at.isoformat() if revision.approved_at else None,
                "training_draft_sha256": module["approved_sha256"], "provider": module["provider"],
            }
            register(db, row, job, "evidence-manifest.json",
                     json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode(), "application/json")
            register(db, row, job, "training-module.json",
                     json.dumps(draft.model_dump(), ensure_ascii=False, sort_keys=True).encode(),
                     "application/json")
            register(db, row, job, "training-module.md", training_markdown(draft), "text/markdown")
            register(db, row, job, "chapter-index.json",
                     json.dumps([item.model_dump() for item in draft.chapters],
                                ensure_ascii=False, sort_keys=True).encode(), "application/json")
            for filename, sections in (
                ("lab-guide.md", ("Prerequisites", "Learning objectives", "Lab steps",
                                  "Validation checkpoints", "Troubleshooting")),
                ("learner-handout.md", ("Prerequisites", "Learning objectives", "Chapters",
                                        "Lab steps", "Validation checkpoints", "Assessment")),
                ("assessment.md", ("Assessment",)),
                ("answer-key.md", ("Answer explanations",)),
            ):
                register(db, row, job, filename, training_markdown(draft, sections), "text/markdown")
        if audio:
            register(db, row, job, "narration.wav", audio.read_bytes(), "audio/wav")
        for fmt, kind in (("srt", "application/x-subrip"), ("vtt", "text/vtt")):
            register(db, row, job, f"narration.{fmt}", media.subtitles(script, fmt), kind)
        register(db, row, job, "provenance.json", json.dumps(provenance, sort_keys=True).encode(), "application/json")
        row.stage = "awaiting_review"
        db.commit()
        return video_id, video_key, provenance["output_sha256"]


def capabilities():
    model = Path(os.environ.get("NARRATION_ASR_MODEL", "/models/asr/faster-whisper-tiny"))
    return {
        "voices": media.VOICES, "speech_engine": "espeak-ng",
        "asr_requires_optional_worker_and_approved_model": True,
        "asr_model_present": (model / "model.bin").is_file(),
        "vision_configured": bool(
            os.environ.get("NARRATION_VISION_URL")
            and os.environ.get("NARRATION_VISION_MODEL")
            and re.fullmatch(
                r"[0-9a-f]{64}", os.environ.get("NARRATION_VISION_MODEL_DIGEST", "")
            )
        ),
        "tts_available_in_this_process": bool(shutil.which("espeak-ng")),
    }
