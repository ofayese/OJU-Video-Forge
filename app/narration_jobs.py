"""Narration stages on the existing durable job/outbox and artifact model."""

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

from sqlalchemy import select

from . import narration as media
from .core import (
    ARTIFACT_ROOT, Audit, MediaAsset, Narration, NarrationRevision,
    ProjectMembership, User, WorkflowArtifact,
)
from .private_storage import private_file, write_private_file


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
                if options.analysis_mode == "transcript"
                else {"status": "manual_not_transcribed", "segments": [], "language": None}
            )
            stage(db, row, "analyzing_scenes", check)
            observations = media.scenes(source, directory, metadata, options, check)
            stage(db, row, "drafting_script", check)
            row.analysis = {
                "media": metadata, "transcript": transcript, "scenes": observations,
                "vision": "local_model_observations_require_review" if options.vision else "unavailable_not_requested",
                "style": options.style, "instructions": options.instructions,
                "language": options.language,
            }
            row.revision += 1
            row.approved_revision = None
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
                or approver.disabled or role not in {"owner", "editor"}
                or job.spec["revision"] != row.revision):
            raise media.NarrationError("approved_script_revision_required")
        script = media.validate_script(revision.script, round(float(metadata["format"]["duration"]) * 1000))
        audio = None
        if options.audio_mode != "preserve":
            stage(db, row, "synthesizing_audio", check)
            audio = media.synthesize(script, directory, options, float(metadata["format"]["duration"]), check)
        stage(db, row, "mixing_audio", check)
        output = directory / "video.mp4"
        result = media.mux(source, audio, output, metadata, options, check)
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
            "output_sha256": media.digest(output), "output_bytes": output.stat().st_size,
        }
        video_id, video_key = register(db, row, job, "video.mp4", output.read_bytes(), "video/mp4")
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
        "vision_configured": bool(os.environ.get("NARRATION_VISION_MODEL")),
        "tts_available_in_this_process": bool(shutil.which("espeak-ng")),
    }
