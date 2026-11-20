import contextlib
import hashlib
import json
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import api, narration, narration_jobs, training
from app.auth import Actor, get_current_actor
from app.config import Settings
from app.core import (
    Base,
    DispatchOutbox,
    Job,
    MediaAsset,
    Narration,
    NarrationRevision,
    Project,
    ProjectMembership,
    User,
    WorkflowArtifact,
)


class CancellationRequestedError(Exception):
    pass


@pytest.fixture
def context(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)

    def database():
        with sessions() as db:
            yield db

    # Direct narration_jobs.execute calls in tests bypass worker coordination;
    # keep them isolated unless a test explicitly exercises coordination.
    monkeypatch.setattr(
        narration_jobs, "heavy_inference_context",
        lambda *a, **k: contextlib.nullcontext(),
    )
    api.app.dependency_overrides[api.db_session] = database
    api.app.dependency_overrides[get_current_actor] = lambda: Actor(
        name="editor", user_id="editor", roles=["viewer"])
    with sessions() as db:
        db.add_all([User(id="editor", username="editor", password_hash="unused"),
                    User(id="viewer", username="viewer", password_hash="unused"),
                    Project(id="project", owner="editor", name="Synthetic")])
        db.flush()
        db.add_all([ProjectMembership(project_id="project", user_id="editor", role="editor"),
                    ProjectMembership(project_id="project", user_id="viewer", role="viewer")])
        db.add(MediaAsset(id="source", project_id="project", filename="synthetic.mp4",
                          media_type="video/mp4", size_bytes=12, sha256="a" * 64,
                          storage_key="uploads/synthetic-source.mp4"))
        db.commit()
    dispatches = []
    monkeypatch.setattr(api, "dispatch_outbox", lambda job_id: dispatches.append(job_id))
    with TestClient(api.app) as client:
        yield client, sessions, dispatches
    api.app.dependency_overrides.clear()
    engine.dispose()


def start(client):
    result = client.post("/v1/projects/project/narrations",
                         headers={"Idempotency-Key": "synthetic-request"},
                         json={"media_id": "source", "options": {"analysis_mode": "manual"}})
    assert result.status_code == 202, result.text
    return result.json()["id"]


def seed_draft(sessions, narration_id):
    script = [{"id": "manual-1", "start_ms": 0, "end_ms": 90000,
               "text": "Synthetic blue screen.", "evidence": ["manual"]}]
    with sessions() as db:
        row = db.get(Narration, narration_id)
        row.revision = 1
        row.stage = "awaiting_script_approval"
        row.analysis = {"media": {"format": {"duration": "90"}}, "scenes": [],
                        "transcript": {"segments": []}}
        db.get(Job, row.job_id).status = "awaiting_review"
        db.add(NarrationRevision(id=str(uuid.uuid4()), narration_id=row.id, revision=1,
                                 editor_id="editor", script=script))
        db.commit()
    return script


def test_submission_is_durable_idempotent_source_scoped_and_never_wan(context):
    client, sessions, dispatches = context
    narration_id = start(client)
    assert start(client) == narration_id
    with sessions() as db:
        row = db.get(Narration, narration_id)
        job = db.get(Job, row.job_id)
        assert job.operation == "narrate_existing_video"
        assert job.media_id == "source"
        assert job.owner_principal_id == "editor"
        assert db.get(DispatchOutbox, job.id).status == "pending"
        assert db.query(Job).count() == 1
    assert len(dispatches) == 1
    missing = client.post("/v1/projects/project/narrations", headers={"Idempotency-Key": "other"},
                          json={"media_id": "other-project-source"})
    assert missing.status_code == 404


def test_training_retry_clears_diagnostics_from_the_previous_attempt(context):
    client, sessions, _ = context
    narration_id = start(client)
    with sessions() as db:
        row = db.get(Narration, narration_id)
        row.stage = "failed"
        row.error = "agent_generation_incomplete"
        row.analysis = {
            "training_generation_diagnostics": {
                "schema_version": 1,
                "stages": [{"stage": "outline", "model_identifier": "old-attempt"}],
            },
        }
        previous = db.get(Job, row.job_id)
        previous.status = "failed"
        previous.spec = {
            **previous.spec,
            "phase": "training_draft",
            "attempt": 0,
            "training": {"source_revision": row.revision},
        }
        db.commit()

    response = client.post(
        f"/v1/projects/project/narrations/{narration_id}/retry",
    )
    assert response.status_code == 202
    assert "training_generation_diagnostics" not in response.json()["analysis"]
    with sessions() as db:
        row = db.get(Narration, narration_id)
        assert "training_generation_diagnostics" not in row.analysis
        retry = db.get(Job, row.job_id)
        assert retry.spec["phase"] == "training_draft"
        assert retry.spec["attempt"] == 1


def test_approval_is_revision_bound_and_edits_invalidate_it(context):
    client, sessions, _ = context
    narration_id = start(client)
    script = seed_draft(sessions, narration_id)
    base = f"/v1/projects/project/narrations/{narration_id}"
    assert client.post(f"{base}/render", json={"expected_revision": 1}).status_code == 409
    approved = client.post(f"{base}/approval",
                           json={"expected_revision": 1, "decision": "approved"})
    assert approved.status_code == 200
    assert approved.json()["approved_revision"] == 1
    script[0]["text"] = "Corrected synthetic description."
    edited = client.put(f"{base}/script", json={"expected_revision": 1, "segments": script})
    assert edited.status_code == 200
    assert edited.json()["revision"] == 2
    assert edited.json()["approved_revision"] is None
    assert client.post(f"{base}/render", json={"expected_revision": 1}).status_code == 409
    assert client.put(f"{base}/script", json={"expected_revision": 1, "segments": script}).status_code == 409
    with sessions() as db:
        first = db.query(NarrationRevision).filter_by(narration_id=narration_id, revision=1).one()
        assert first.approved_by == "editor"
        assert first.script[0]["text"] == "Synthetic blue screen."


def test_viewer_cannot_edit_approve_or_render_and_foreign_reader_is_denied(context):
    client, sessions, _ = context
    narration_id = start(client)
    script = seed_draft(sessions, narration_id)
    api.app.dependency_overrides[get_current_actor] = lambda: Actor(
        name="viewer", user_id="viewer", roles=["viewer"])
    base = f"/v1/projects/project/narrations/{narration_id}"
    assert client.put(f"{base}/script", json={"expected_revision": 1, "segments": script}).status_code == 403
    assert client.post(f"{base}/approval", json={"expected_revision": 1, "decision": "approved"}).status_code == 403
    assert client.post(f"{base}/render", json={"expected_revision": 1}).status_code == 403
    api.app.dependency_overrides[get_current_actor] = lambda: Actor(
        name="foreign", user_id="foreign", roles=["viewer"])
    assert client.get(base).status_code == 404


def test_artifact_listing_is_narration_scoped(context):
    client, sessions, _ = context
    narration_id = start(client)
    with sessions() as db:
        row = db.get(Narration, narration_id)
        db.add(WorkflowArtifact(id="ours", job_id=row.job_id, project_id="project",
                                owner="editor", relative_path="ours/video.mp4",
                                filename="video.mp4", media_type="video/mp4",
                                sha256="a" * 64, size_bytes=12))
        db.add(Job(id="wan-job", project_id="project", owner="editor", operation="wan22_t2v",
                   status="awaiting_review", spec={}))
        db.flush()
        db.add(WorkflowArtifact(id="unrelated", job_id="wan-job", project_id="project",
                                owner="editor", relative_path="other/video.mp4", filename="video.mp4",
                                media_type="video/mp4", sha256="b" * 64, size_bytes=12))
        db.commit()
    result = client.get(f"/v1/projects/project/narrations/{narration_id}").json()
    assert [item["id"] for item in result["artifacts"]] == ["ours"]


def test_narration_job_artifact_uses_project_scoped_download(context, tmp_path, monkeypatch):
    client, sessions, _ = context
    narration_id = start(client)
    content = b"synthetic narration video"
    digest = hashlib.sha256(content).hexdigest()
    with sessions() as db:
        row = db.get(Narration, narration_id)
        job = db.get(Job, row.job_id)
        artifact_id = str(uuid.uuid4())
        relative_path = f"{job.id}/{artifact_id}/video.mp4"
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
        job.status = "awaiting_review"
        job.artifact = relative_path
        job.artifact_id = artifact_id
        job.artifact_sha256 = digest
        db.add(WorkflowArtifact(
            id=artifact_id, job_id=job.id, project_id=row.project_id, owner=job.owner,
            relative_path=relative_path, filename="video.mp4", media_type="video/mp4",
            sha256=digest, size_bytes=len(content),
        ))
        db.commit()
        job_id = job.id
    monkeypatch.setattr(api, "ARTIFACT_ROOT", tmp_path)

    job = client.get(f"/v1/jobs/{job_id}").json()
    assert job["artifact"] == f"/v1/projects/project/artifacts/{artifact_id}"
    assert job["project_artifact_id"] == artifact_id
    response = client.get(job["artifact"])
    assert response.status_code == 200
    assert response.content == content
    assert response.headers["X-Content-SHA256"] == digest


@pytest.mark.parametrize("script", [
    [{"id": "a", "start_ms": 0, "end_ms": 90001, "text": "Too long"}],
    [{"id": "a", "start_ms": 0, "end_ms": 1000, "text": "One"},
     {"id": "b", "start_ms": 500, "end_ms": 1500, "text": "Overlap"}],
])
def test_script_timing_is_bounded(script):
    with pytest.raises(narration.NarrationError):
        narration.validate_script(script, 90000)


def test_no_audio_does_not_invent_transcript_or_require_model(tmp_path):
    result = narration.transcribe(tmp_path / "synthetic.mp4", tmp_path,
                                  {"streams": [{"codec_type": "video"}]}, lambda: None)
    assert result["status"] == "no_audio"
    assert result["segments"] == []


def test_missing_asr_model_is_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("NARRATION_ASR_MODEL", str(tmp_path / "missing"))
    with pytest.raises(narration.NarrationError, match="asr_model_unavailable"):
        narration.transcribe(tmp_path / "synthetic.mp4", tmp_path,
                             {"streams": [{"codec_type": "audio"}]}, lambda: None)


def test_cancel_before_subprocess_does_not_start_tool(monkeypatch):
    def cancelled():
        raise RuntimeError("cancelled")
    monkeypatch.setattr(narration.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("Tool started"))
    with pytest.raises(RuntimeError, match="cancelled"):
        narration.run(["ffmpeg"], cancelled)


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_subprocess_output_limit_kills_tool(monkeypatch, stream):
    child_processes = []
    popen = narration.subprocess.Popen

    def tracked_popen(*args, **kwargs):
        process = popen(*args, **kwargs)
        child_processes.append(process)
        return process

    monkeypatch.setattr(narration.subprocess, "Popen", tracked_popen)
    code = (f"import sys,time; sys.{stream}.write('x' * {2 * 1024 * 1024 + 1}); "
            f"sys.{stream}.flush(); time.sleep(30)")
    with pytest.raises(narration.NarrationError, match="media_diagnostics_limit"):
        narration.run([sys.executable, "-c", code], lambda: None, timeout=5)
    assert child_processes[0].poll() is not None


def test_subprocess_timeout_kills_and_reaps_tool(monkeypatch):
    child_processes = []
    popen = narration.subprocess.Popen

    def tracked_popen(*args, **kwargs):
        process = popen(*args, **kwargs)
        child_processes.append(process)
        return process

    monkeypatch.setattr(narration.subprocess, "Popen", tracked_popen)
    with pytest.raises(narration.NarrationError, match="media_timeout"):
        narration.run([sys.executable, "-c", "import time; time.sleep(30)"],
                      lambda: None, timeout=0.1)
    assert child_processes[0].poll() is not None


def test_subprocess_cancellation_kills_and_reaps_tool(monkeypatch):
    child_processes = []
    popen = narration.subprocess.Popen

    def tracked_popen(*args, **kwargs):
        process = popen(*args, **kwargs)
        child_processes.append(process)
        return process

    def check():
        if child_processes:
            raise RuntimeError("cancelled")

    monkeypatch.setattr(narration.subprocess, "Popen", tracked_popen)
    with pytest.raises(RuntimeError, match="cancelled"):
        narration.run([sys.executable, "-c", "import time; time.sleep(30)"], check)
    assert child_processes[0].poll() is not None


def test_timing_overrun_requires_revision_instead_of_truncation(monkeypatch, tmp_path):
    monkeypatch.setattr(narration.shutil, "which", lambda _name: "/usr/bin/espeak-ng")
    monkeypatch.setattr(narration, "run", lambda *_args, **_kwargs: b"")
    monkeypatch.setattr(narration, "probe", lambda *_args: {"format": {"duration": "3"}})
    with pytest.raises(narration.NarrationError, match="timing_revision_required"):
        narration.synthesize(
            [{"id": "too-short", "start_ms": 0, "end_ms": 1000, "text": "A spoken sentence."}],
            tmp_path, narration.NarrationOptions(), 10, lambda: None)


def test_script_can_be_corrected_after_failed_synthesis(context):
    client, sessions, _ = context
    narration_id = start(client)
    script = seed_draft(sessions, narration_id)
    with sessions() as db:
        row = db.get(Narration, narration_id)
        row.stage = "failed"
        row.error = "timing_revision_required"
        db.get(Job, row.job_id).status = "failed"
        db.commit()
    base = f"/v1/projects/project/narrations/{narration_id}"
    edited = client.put(f"{base}/script", json={"expected_revision": 1, "segments": script})
    assert edited.status_code == 200
    assert client.get(base).json()["stage"] == "awaiting_script_approval"
    approved = client.post(f"{base}/approval",
                           json={"expected_revision": 2, "decision": "approved"})
    assert approved.status_code == 200


def test_draft_does_not_invent_source_events():
    analysis = {
        "media": {"format": {"duration": "90"}}, "scenes": [],
        "transcript": {"segments": [{"id": "speech-1", "start_ms": 0, "end_ms": 2000,
                                     "text": "A synthetic instruction."}]},
    }
    script = narration.draft(analysis, narration.NarrationOptions(instructions="Invent a procedure"))
    assert script[0]["text"] == "A synthetic instruction."
    assert script[0]["evidence"] == ["speech-1"]
    assert "hypothesis" in script[0]["uncertainty"]


def test_silent_training_requires_evidence_review_before_script_or_audio(context, monkeypatch, tmp_path):
    client, sessions, _ = context
    response = client.post(
        "/v1/projects/project/narrations", headers={"Idempotency-Key": "training"},
        json={"media_id": "source", "options": {"workflow": "silent_training"}},
    )
    assert response.status_code == 202
    narration_id = response.json()["id"]
    source = tmp_path / "source" / "video.mp4"
    narration_jobs.write_private_file(tmp_path, "source/video.mp4", b"synthetic123")
    source_hash = narration.digest(source)
    monkeypatch.setattr(narration_jobs, "ARTIFACT_ROOT", tmp_path)
    monkeypatch.setattr(narration, "probe", lambda *_args: {
        "format": {"duration": "90"}, "streams": [{"codec_type": "video"}],
    })
    def synthetic_scenes(_source, directory, *_args):
        frames = []
        for index, text in enumerate(("Menu", "Other")):
            path = directory / f"frame-{index}.jpg"
            path.write_bytes(synthetic_jpeg())
            frames.append({
                "id": f"frame-{index}", "timestamp_ms": index * 45000,
                "frame_sha256": narration.digest(path), "ocr_status": "complete",
                "vision_status": "not_requested", "ocr": text,
            })
        return frames

    monkeypatch.setattr(narration, "scenes", synthetic_scenes)

    def unexpected_transcription(*_args):
        raise AssertionError("Silent training must not run Whisper")

    monkeypatch.setattr(narration, "transcribe", unexpected_transcription)
    with sessions() as db:
        row = db.get(Narration, narration_id)
        row.source_sha256 = source_hash
        asset = db.get(MediaAsset, row.media_id)
        asset.sha256 = source_hash
        asset.storage_key = "source/video.mp4"
        db.commit()
        narration_jobs.execute(db, db.get(Job, row.job_id), lambda: None)

    base = f"/v1/projects/project/narrations/{narration_id}"
    result = client.get(base).json()
    from app import adapters as _adapters
    monkeypatch.setattr(api, "get_settings", lambda: Settings(agents_enabled=False))
    monkeypatch.setattr(narration_jobs, "get_settings", lambda: Settings(agents_enabled=False), raising=False)
    monkeypatch.setattr(_adapters, "get_settings", lambda: Settings(agents_enabled=False))
    assert client.get(f"{base}/training-agent").json()["status"] == "disabled"
    monkeypatch.setattr(api, "get_settings", lambda: Settings(agents_enabled=True))
    monkeypatch.setattr(_adapters, "get_settings", lambda: Settings(
        agents_enabled=True,
        training_text_provider="",
        training_text_url="",
        training_text_model="",
        training_text_model_digest="",
    ))
    status = client.get(f"{base}/training-agent").json()
    assert status["status"] == "unconfigured"
    assert status.get("reason") == "training_text_unconfigured"
    assert result["stage"] == "awaiting_procedure_review"
    assert result["script"] == []
    screenshot_id = result["analysis"]["scenes"][0]["artifact_id"]
    monkeypatch.setattr(api, "ARTIFACT_ROOT", tmp_path)
    assert client.get(f"/v1/projects/project/artifacts/{screenshot_id}").content == synthetic_jpeg()
    assert client.post(f"{base}/render", json={"expected_revision": 1}).status_code == 409
    steps = result["analysis"]["procedure"]["steps"]
    assert client.post(f"{base}/procedure-review", json={
        "expected_revision": 1, "steps": steps,
    }).status_code == 422
    steps[0]["decision"] = "accepted"
    steps[0]["description"] = "The menu is visible."
    steps[0]["confidence"] = "medium"
    steps[0]["evidence_category"] = "direct_observation"
    steps[1]["decision"] = "rejected"
    altered = [{**steps[0], "evidence": ["other-frame"]}]
    assert client.post(f"{base}/procedure-review", json={
        "expected_revision": 1, "steps": altered,
    }).status_code == 422
    api.app.dependency_overrides[get_current_actor] = lambda: Actor(
        name="viewer", user_id="viewer", roles=["viewer"])
    assert client.post(f"{base}/procedure-review", json={
        "expected_revision": 1, "steps": steps,
    }).status_code == 403
    with sessions() as db:
        db.add(User(id="reviewer", username="reviewer", password_hash="unused"))
        db.flush()
        db.add(ProjectMembership(project_id="project", user_id="reviewer", role="reviewer"))
        db.commit()
    api.app.dependency_overrides[get_current_actor] = lambda: Actor(
        name="reviewer", user_id="reviewer", roles=["viewer"])
    reviewed = client.post(f"{base}/procedure-review", json={
        "expected_revision": 1, "steps": steps, "comment": "Reviewed source.",
    })
    assert reviewed.status_code == 200
    assert reviewed.json()["analysis"]["procedure"]["status"] == "approved"
    assert reviewed.json()["stage"] == "evidence_approved"
    assert reviewed.json()["script"] == []
    api.app.dependency_overrides[get_current_actor] = lambda: Actor(
        name="editor", user_id="editor", roles=["viewer"])
    assert client.post(f"{base}/procedure-review", json={
        "expected_revision": 1, "steps": steps,
    }).status_code == 409
    assert client.post(f"{base}/render", json={"expected_revision": 2}).status_code == 409
    assert client.post(f"{base}/training-draft", json={"expected_revision": 2}).status_code == 503

    claim = {"text": "The menu is visible.", "kind": "evidence", "evidence": ["frame-0"]}
    timed = {**claim, "id": "segment-1", "start_ms": 0, "end_ms": 45000}

    class SyntheticAdapter:
        def generate_training(self, evidence, check):
            check()
            assert evidence["approved_steps"][0]["description"] == "The menu is visible."
            from app.training import TrainingDraft, TrainingGeneration

            draft = TrainingDraft.model_validate({
                "title": "Synthetic module", "audience": "Beginner", "prerequisites": [],
                "learning_objectives": [claim], "chapters": [timed], "script": [timed],
                "annotations": [], "technical_explanations": [], "lab_steps": [],
                "validation_checkpoints": [], "troubleshooting": [], "assessment": [],
                "answer_explanations": [],
            })
            return TrainingGeneration(
                draft=draft,
                stages=[{
                    "stage": stage,
                    "schema_version": 1,
                    "model_identifier": "test",
                    "model_digest": "a" * 64,
                    "endpoint_sha256": "b" * 64,
                    "ollama_version": "synthetic",
                    "api_route": "/api/chat",
                    "thinking_requested": False,
                    "schema_validation_result": "passed",
                    "evidence_validation_result": "passed",
                } for stage in ("outline", "procedure", "narration", "activities")],
            )

        def provenance(self):
            return {
                "provider": "synthetic", "model": "test",
                "model_digest": "a" * 64, "endpoint_sha256": "b" * 64,
            }

    monkeypatch.setattr(api, "get_settings", lambda: Settings(
        agents_enabled=True,
        training_text_provider="ollama",
        training_text_url="http://ollama:11434",
        training_text_model="synthetic",
        training_text_model_digest="a" * 64,
    ))
    monkeypatch.setattr(_adapters, "get_settings", lambda: Settings(
        agents_enabled=True,
        training_text_provider="ollama",
        training_text_url="http://ollama:11434",
        training_text_model="synthetic",
        training_text_model_digest="a" * 64,
    ))
    monkeypatch.setattr(_adapters, "get_training_text_adapter", lambda: SyntheticAdapter())
    monkeypatch.setattr(narration_jobs, "get_training_text_adapter", lambda: SyntheticAdapter())
    assert client.get(f"{base}/training-agent").json()["status"] in {
        "configured", "ready", "degraded", "unchecked", "unverified"
    }
    queued = client.post(f"{base}/training-draft", json={"expected_revision": 2})
    assert queued.status_code == 202
    with sessions() as db:
        narration_jobs.execute(db, db.get(Job, queued.json()["job_id"]), lambda: None)
    drafted = client.get(base).json()
    assert drafted["stage"] == "awaiting_script_approval"
    assert drafted["script"][0]["text"] == "The menu is visible."
    assert drafted["analysis"]["training_module"]["provider"]["provider"] == "synthetic"
    provenance = drafted["analysis"]["training_module"]["provenance"]
    assert provenance["review_status"] == "pending_human_review"
    assert provenance["source_video_sha256"] == source_hash
    assert provenance["approved_procedure_revision"] == 2
    script_provenance = next(item for item in provenance["elements"] if item["path"] == "script[0]")
    assert script_provenance["evidence_ids"] == ["frame-0"]
    assert script_provenance["source_timestamps_ms"] == [{"evidence_id": "frame-0", "timestamp_ms": 0}]
    assert script_provenance["review_status"] == "pending_human_review"
    altered_script = [{**drafted["script"][0], "start_ms": 45000, "end_ms": 90000}]
    assert client.put(f"{base}/script", json={
        "expected_revision": 3, "segments": altered_script,
    }).status_code == 422
    draft = drafted["analysis"]["training_module"]["draft"]
    invalid_draft = {**draft, "learning_objectives": [
        {"text": "Other screen", "kind": "evidence", "evidence": ["frame-1"]},
    ]}
    assert client.put(f"{base}/training-draft", json={
        "expected_revision": 3, "draft": invalid_draft,
    }).status_code == 422
    api.app.dependency_overrides[get_current_actor] = lambda: Actor(
        name="viewer", user_id="viewer", roles=["viewer"])
    assert client.put(f"{base}/training-draft", json={
        "expected_revision": 3, "draft": draft,
    }).status_code == 403
    api.app.dependency_overrides[get_current_actor] = lambda: Actor(
        name="editor", user_id="editor", roles=["viewer"])
    draft["title"] = "Reviewed synthetic module"
    edited = client.put(f"{base}/training-draft", json={
        "expected_revision": 3, "draft": draft,
    })
    assert edited.status_code == 200
    assert edited.json()["revision"] == 4
    assert edited.json()["analysis"]["training_module"]["draft"]["title"] == "Reviewed synthetic module"
    edited_provenance = edited.json()["analysis"]["training_module"]["provenance"]
    title_provenance = next(item for item in edited_provenance["elements"] if item["path"] == "title")
    assert title_provenance["generation_stage"] == "human_edit"
    assert edited_provenance["review_status"] == "pending_human_review"
    assert client.post(f"{base}/approval", json={
        "expected_revision": 3, "decision": "approved",
    }).status_code == 409
    assert client.post(f"{base}/render", json={"expected_revision": 3}).status_code == 409
    api.app.dependency_overrides[get_current_actor] = lambda: Actor(
        name="reviewer", user_id="reviewer", roles=["viewer"])
    assert client.post(f"{base}/approval", json={
        "expected_revision": 4, "decision": "approved",
    }).status_code == 200
    approved = client.get(base).json()["analysis"]["training_module"]["provenance"]
    assert approved["review_status"] == "human_approved"
    assert all(item["review_status"] == "human_approved" for item in approved["elements"])
    api.app.dependency_overrides[get_current_actor] = lambda: Actor(
        name="editor", user_id="editor", roles=["viewer"])
    assert client.post(f"{base}/render", json={
        "expected_revision": 4, "render_annotations": True,
    }).status_code == 422
    api.app.dependency_overrides[get_current_actor] = lambda: Actor(
        name="reviewer", user_id="reviewer", roles=["viewer"])
    reopened = client.post(f"{base}/procedure-reopen", json={"expected_revision": 4})
    assert reopened.status_code == 200
    assert reopened.json()["approved_revision"] is None
    assert "training_module" not in reopened.json()["analysis"]
    history = reopened.json()["analysis"]["procedure"]["review_history"]
    assert len(history) == 1
    assert history[0]["evidence_version"] == 1
    assert history[0]["reviewed_by"] == "reviewer"
    assert history[0]["comment"] == "Reviewed source."
    api.app.dependency_overrides[get_current_actor] = lambda: Actor(
        name="editor", user_id="editor", roles=["viewer"])
    assert client.post(f"{base}/render", json={"expected_revision": 3}).status_code == 409


def test_render_profile_default_source_passthrough(context):
    client, sessions, _ = context
    narration_id = start(client)
    seed_draft(sessions, narration_id)
    base = f"/v1/projects/project/narrations/{narration_id}"
    assert client.post(f"{base}/approval", json={
        "expected_revision": 1, "decision": "approved",
    }).status_code == 200
    response = client.post(f"{base}/render", json={
        "expected_revision": 1, "render_profile": "source_passthrough",
    })
    assert response.status_code == 202, response.text


def test_render_profile_explicit_h264_aac(context):
    client, sessions, _ = context
    narration_id = start(client)
    seed_draft(sessions, narration_id)
    base = f"/v1/projects/project/narrations/{narration_id}"
    assert client.post(f"{base}/approval", json={
        "expected_revision": 1, "decision": "approved",
    }).status_code == 200
    response = client.post(f"{base}/render", json={
        "expected_revision": 1, "render_profile": "h264_aac",
    })
    assert response.status_code == 202, response.text


def test_render_profile_invalid_rejected(context):
    client, sessions, _ = context
    narration_id = start(client)
    seed_draft(sessions, narration_id)
    base = f"/v1/projects/project/narrations/{narration_id}"
    assert client.post(f"{base}/approval", json={
        "expected_revision": 1, "decision": "approved",
    }).status_code == 200
    response = client.post(f"{base}/render", json={
        "expected_revision": 1, "render_profile": "invalid_profile",
    })
    assert response.status_code == 422, response.text


def test_generation_stage_provenance_rejects_missing_or_mismatched_stages():
    provider = {
        "provider": "ollama", "model": "qwen3-vl:4b",
        "model_digest": "a" * 64, "endpoint_sha256": "b" * 64,
    }
    stages = [{
        "stage": stage, "schema_version": 1,
        "model_identifier": provider["model"],
        "model_digest": provider["model_digest"],
        "endpoint_sha256": provider["endpoint_sha256"],
        "ollama_version": "0.40.16", "api_route": "/api/chat",
        "thinking_requested": False,
        "schema_validation_result": "passed",
        "evidence_validation_result": "passed",
    } for stage in ("outline", "procedure", "narration", "activities")]
    narration_jobs._validate_generation_stage_provenance(stages, provider, 1)

    with pytest.raises(ValueError, match="stages_incomplete"):
        narration_jobs._validate_generation_stage_provenance(stages[:-1], provider, 1)
    with pytest.raises(ValueError, match="stage_provenance_invalid"):
        narration_jobs._validate_generation_stage_provenance(
            [{**stage, "model_digest": "c" * 64} if index == 1 else stage
             for index, stage in enumerate(stages)],
            provider,
            1,
        )


def test_training_render_rechecks_procedure_approver_membership(context, monkeypatch, tmp_path):
    client, sessions, _ = context
    response = client.post(
        "/v1/projects/project/narrations", headers={"Idempotency-Key": "revoked-review"},
        json={"media_id": "source", "options": {"workflow": "silent_training"}},
    )
    narration_id = response.json()["id"]
    narration_jobs.write_private_file(tmp_path, "source/video.mp4", b"synthetic123")
    source_hash = narration.digest(tmp_path / "source" / "video.mp4")
    monkeypatch.setattr(narration_jobs, "ARTIFACT_ROOT", tmp_path)
    monkeypatch.setattr(narration, "probe", lambda *_args: {
        "format": {"duration": "90"}, "streams": [{"codec_type": "video"}],
    })
    with sessions() as db:
        db.add(User(id="former-reviewer", username="former-reviewer", password_hash="unused"))
        row = db.get(Narration, narration_id)
        row.source_sha256 = source_hash
        row.revision = row.approved_revision = 1
        row.analysis = {
            "scenes": [{"id": "frame-0"}],
            "procedure": {
                "status": "approved", "approved_by": "former-reviewer",
                "steps": [{"id": "step-1", "start_ms": 0, "end_ms": 90000,
                           "description": "A menu is visible.", "evidence": ["frame-0"],
                           "uncertainty": ["visual"], "decision": "accepted", "confidence": "medium"}],
            },
        }
        asset = db.get(MediaAsset, row.media_id)
        asset.sha256 = source_hash
        asset.storage_key = "source/video.mp4"
        job = db.get(Job, row.job_id)
        job.spec = {**job.spec, "phase": "render", "revision": 1}
        db.add(NarrationRevision(
            id=str(uuid.uuid4()), narration_id=row.id, revision=1, editor_id="editor",
            approved_by="editor", script=[{
                "id": "segment", "start_ms": 0, "end_ms": 1000,
                "text": "A menu is visible.", "evidence": ["frame-0"],
            }],
        ))
        db.commit()
        with pytest.raises(narration.NarrationError, match="approved_procedure_required"):
            narration_jobs.execute(db, job, lambda: None)


def test_training_render_rejects_tampering_and_exports_reviewed_package(context, monkeypatch, tmp_path):
    client, sessions, _ = context
    response = client.post(
        "/v1/projects/project/narrations", headers={"Idempotency-Key": "package"},
        json={"media_id": "source", "options": {"workflow": "silent_training", "audio_mode": "preserve"}},
    )
    narration_id = response.json()["id"]
    narration_jobs.write_private_file(tmp_path, "source/video.mp4", b"synthetic123")
    source_hash = narration.digest(tmp_path / "source" / "video.mp4")
    monkeypatch.setattr(narration_jobs, "ARTIFACT_ROOT", tmp_path)
    monkeypatch.setattr(api, "ARTIFACT_ROOT", tmp_path)
    monkeypatch.setattr(narration, "probe", lambda *_args: {
        "format": {"duration": "90"}, "streams": [{"codec_type": "video"}],
    })
    monkeypatch.setattr(narration, "mux", lambda _src, _audio, output, *_args: (
        output.write_bytes(b"synthetic output") and {"format": {"duration": "90"}}
    ))
    monkeypatch.setattr(narration, "video_hash", lambda *_args: "visual-hash")
    monkeypatch.setattr(narration, "run", lambda *_args: b"ffmpeg synthetic")
    claim = {"text": "A menu is visible.", "kind": "evidence", "evidence": ["frame-0"]}
    timed = {**claim, "id": "segment", "start_ms": 0, "end_ms": 1000}
    draft = {
        "title": "Synthetic module", "audience": "Beginner", "prerequisites": [],
        "learning_objectives": [claim], "chapters": [timed], "script": [timed],
        "annotations": [], "technical_explanations": [], "lab_steps": [],
        "validation_checkpoints": [], "troubleshooting": [],
        "assessment": [{"text": "What is visible?", "kind": "evidence", "evidence": ["frame-0"]}],
        "answer_explanations": [{"text": "A blue <screen>.", "kind": "supplemental", "evidence": []}],
    }
    with sessions() as db:
        row = db.get(Narration, narration_id)
        row.source_sha256 = source_hash
        row.revision = row.approved_revision = 1
        procedure = {
            "status": "approved", "approved_by": "editor",
            "steps": [{"id": "step-1", "start_ms": 0, "end_ms": 90000,
                       "description": "A menu is visible.", "evidence": ["frame-0"],
                       "uncertainty": ["visual"], "decision": "accepted", "confidence": "medium"}],
        }
        module = {
            "draft": draft, "approved_sha256": training.draft_digest(draft),
            "provider": {"provider": "synthetic", "model": "test", "model_digest": "a" * 64},
        }
        row.analysis = {"scenes": [{"id": "frame-0", "timestamp_ms": 0, "frame_sha256": "b" * 64}],
                        "procedure": procedure, "training_module": module, "transcript": {}}
        asset = db.get(MediaAsset, row.media_id)
        asset.sha256 = source_hash
        asset.storage_key = "source/video.mp4"
        job = db.get(Job, row.job_id)
        job.spec = {**job.spec, "phase": "render", "revision": 1}
        db.add(NarrationRevision(
            id=str(uuid.uuid4()), narration_id=row.id, revision=1, editor_id="editor",
            approved_by="editor", script=narration.validate_script(training.draft_script(
                training.TrainingDraft.model_validate(draft)), 90000),
        ))
        db.commit()
        row.analysis = {**row.analysis, "training_module": {
            **module, "draft": {**draft, "title": "Tampered"},
        }}
        db.commit()
        with pytest.raises(narration.NarrationError, match="training_approval_or_evidence_invalid"):
            narration_jobs.execute(db, job, lambda: None)
        row.analysis = {**row.analysis, "training_module": module}
        db.commit()
        narration_jobs.execute(db, job, lambda: None)
        artifacts = {item.filename: item for item in db.query(WorkflowArtifact).filter_by(job_id=job.id)}
        assert {"video.mp4", "procedure.json", "teaching-script.json", "evidence-manifest.json",
                "training-module.json", "training-module.md", "chapter-index.json", "lab-guide.md",
                "learner-handout.md", "assessment.md", "answer-key.md"} <= artifacts.keys()
        manifest_id = artifacts["evidence-manifest.json"].id
        handout_id = artifacts["learner-handout.md"].id
        answer_id = artifacts["answer-key.md"].id
    manifest = client.get(f"/v1/projects/project/artifacts/{manifest_id}")
    assert manifest.status_code == 200
    assert manifest.json()["source_sha256"] == source_hash
    assert manifest.json()["frames"][0]["frame_sha256"] == "b" * 64
    assert manifest.json()["training_draft_sha256"] == training.draft_digest(draft)
    handout = client.get(f"/v1/projects/project/artifacts/{handout_id}").text
    answer = client.get(f"/v1/projects/project/artifacts/{answer_id}").text
    assert "What is visible?" in handout and "A blue" not in handout
    assert "A blue &lt;screen&gt;" in answer and "<screen>" not in answer


@pytest.mark.parametrize("mode,speech,failure", [
    ("transcript", True, narration.NarrationError("vision_unavailable")),
    ("transcript", True, httpx.ReadTimeout("local vision timed out")),
    ("manual", False, narration.NarrationError("ocr_service_unavailable")),
    ("transcript", False, narration.NarrationError("vision_unavailable")),
    ("transcript", True, CancellationRequestedError()),
])
def test_optional_visual_failure_preserves_narration_or_cancellation(
    context, monkeypatch, tmp_path, mode, speech, failure,
):
    client, sessions, _ = context
    narration_id = start(client)
    source = tmp_path / "source" / "video.mp4"
    narration_jobs.write_private_file(tmp_path, "source/video.mp4", b"synthetic123")
    source_hash = narration.digest(source)
    monkeypatch.setattr(narration_jobs, "ARTIFACT_ROOT", tmp_path)
    monkeypatch.setattr(narration, "probe", lambda *_args: {
        "format": {"duration": "90"}, "streams": [{"codec_type": "video"}],
    })
    transcript = {
        "status": "transcribed" if speech else "no_audio", "language": "en" if speech else None,
        "segments": [{"id": "speech-1", "start_ms": 0, "end_ms": 2000,
                      "text": "Verified synthetic speech."}] if speech else [],
    }

    def transcribe(*_args):
        assert mode == "transcript"
        return transcript

    def unavailable(*_args):
        raise failure

    monkeypatch.setattr(narration, "transcribe", transcribe)
    monkeypatch.setattr(narration, "scenes", unavailable)
    with sessions() as db:
        row = db.get(Narration, narration_id)
        row.source_sha256 = source_hash
        row.options = {**row.options, "analysis_mode": mode, "vision": True}
        asset = db.get(MediaAsset, row.media_id)
        asset.sha256 = source_hash
        asset.storage_key = "source/video.mp4"
        db.commit()
        job = db.get(Job, row.job_id)
        if isinstance(failure, CancellationRequestedError):
            with pytest.raises(CancellationRequestedError):
                narration_jobs.execute(db, job, lambda: None)
            assert db.query(NarrationRevision).count() == 0
            return
        narration_jobs.execute(db, job, lambda: None)
        assert row.stage == "awaiting_script_approval"
        assert row.error is None
        assert row.analysis["scene_analysis"]["status"] == "unavailable"
        assert row.analysis["scene_analysis"]["error"]
        assert row.analysis["scenes"] == []
        revision = db.query(NarrationRevision).filter_by(narration_id=row.id).one()
        assert revision.script[0]["text"] == ("Verified synthetic speech." if speech else "")
        if speech:
            assert row.analysis["transcript"] == transcript
            assert revision.script[0]["evidence"] == ["speech-1"]


def mock_ocr_client(monkeypatch, handler):
    client_type = httpx.Client
    captured = {}
    transport = httpx.MockTransport(handler)

    def create_client(**kwargs):
        captured.update(kwargs)
        return client_type(transport=transport, **kwargs)

    monkeypatch.setattr(narration.httpx, "Client", create_client)
    return captured


def synthetic_jpeg(width=2, height=2):
    return (
        b"\xff\xd8\xff\xc0\x00\x0b\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x01\x01\x11\x00\xff\xd9"
    )


@contextlib.contextmanager
def _active_inference_lease_context(monkeypatch):
    from app import inference_coordination as ic
    lease = ic.InferenceLease(owner="test-owner")
    monkeypatch.setattr(ic, "ensure_lease_owned", lambda *_a, **_k: None)
    token = ic._set_active_lease(lease)
    try:
        yield lease
    finally:
        ic._reset_active_lease(token)


def test_vision_pins_model_digest_and_allows_gpu_inference(monkeypatch, tmp_path):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(synthetic_jpeg())
    model = "qwen3-vl:4b"
    expected_digest = "a" * 64
    paths = []

    def handle(request):
        paths.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{
                "name": model, "digest": expected_digest,
            }]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "vision"]})
        payload = json.loads(request.content)
        assert payload["think"] is False
        assert payload["options"]["num_ctx"] == 4096
        assert payload["options"]["num_predict"] == 512
        assert "num_gpu" not in payload["options"]
        assert request.url.path == "/api/chat"
        assert payload["messages"][0]["images"] == [
            narration.base64.b64encode(frame.read_bytes()).decode()
        ]
        return httpx.Response(200, json={
            "done": True, "done_reason": "stop",
            "message": {"content": json.dumps({
                "observation": "Save button visible", "uncertain": True,
            })},
        })

    mock_ocr_client(monkeypatch, handle)
    monkeypatch.setenv("NARRATION_VISION_URL", "http://ollama:11434")
    monkeypatch.setenv("NARRATION_VISION_MODEL", model)
    monkeypatch.setenv("NARRATION_VISION_MODEL_DIGEST", expected_digest)

    with _active_inference_lease_context(monkeypatch):
        result = narration.vision(frame, lambda: None)

    assert paths == [
        "/api/tags", "/api/show", "/api/chat", "/api/tags", "/api/show",
    ]
    assert result["text"] == "Save button visible"
    assert result["model_digest"] == expected_digest
    assert result["model_info_sha256"] == hashlib.sha256(
        httpx.Response(200, json={"capabilities": ["completion", "vision"]}).content
    ).hexdigest()


def test_vision_rejects_token_truncated_chat_response(monkeypatch, tmp_path):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(synthetic_jpeg())
    expected_digest = "a" * 64

    def handle(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{
                "name": "qwen3-vl:4b", "digest": expected_digest,
            }]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["vision"]})
        return httpx.Response(200, json={
            "done": True, "done_reason": "length",
            "message": {"content": json.dumps({
                "observation": "Save button visible", "uncertain": True,
            })},
        })

    mock_ocr_client(monkeypatch, handle)
    monkeypatch.setenv("NARRATION_VISION_URL", "http://ollama:11434")
    monkeypatch.setenv("NARRATION_VISION_MODEL", "qwen3-vl:4b")
    monkeypatch.setenv("NARRATION_VISION_MODEL_DIGEST", expected_digest)

    with (
        pytest.raises(narration.NarrationError, match="invalid_visual_observation"),
        _active_inference_lease_context(monkeypatch),
    ):
        narration.vision(frame, lambda: None)


def test_vision_rejects_mismatched_model_digest_before_generation(monkeypatch, tmp_path):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(synthetic_jpeg())
    paths = []

    def handle(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"models": [{
            "name": "qwen3-vl:4b", "digest": "b" * 64,
        }]})

    mock_ocr_client(monkeypatch, handle)
    monkeypatch.setenv("NARRATION_VISION_URL", "http://ollama:11434")
    monkeypatch.setenv("NARRATION_VISION_MODEL", "qwen3-vl:4b")
    monkeypatch.setenv("NARRATION_VISION_MODEL_DIGEST", "a" * 64)

    with pytest.raises(
        narration.NarrationError, match="vision_model_identity_unavailable_or_changed"
    ), _active_inference_lease_context(monkeypatch):
        narration.vision(frame, lambda: None)

    assert paths == ["/api/tags"]


def test_vision_rejects_identity_change_after_generation(monkeypatch, tmp_path):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(synthetic_jpeg())
    expected_digest = "a" * 64
    tag_calls = 0

    def handle(request):
        nonlocal tag_calls
        if request.url.path == "/api/tags":
            tag_calls += 1
            digest = expected_digest if tag_calls == 1 else "b" * 64
            return httpx.Response(200, json={"models": [{
                "name": "qwen3-vl:4b", "digest": digest,
            }]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["vision"]})
        return httpx.Response(200, json={
            "done": True, "done_reason": "stop",
            "message": {"content": json.dumps({
                "observation": "Save button visible", "uncertain": True,
            })},
        })

    mock_ocr_client(monkeypatch, handle)
    monkeypatch.setenv("NARRATION_VISION_URL", "http://ollama:11434")
    monkeypatch.setenv("NARRATION_VISION_MODEL", "qwen3-vl:4b")
    monkeypatch.setenv("NARRATION_VISION_MODEL_DIGEST", expected_digest)

    with pytest.raises(
        narration.NarrationError, match="vision_model_identity_unavailable_or_changed"
    ), _active_inference_lease_context(monkeypatch):
        narration.vision(frame, lambda: None)


def test_extract_ocr_uses_bounded_docling_rapidocr_request(monkeypatch, tmp_path):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(synthetic_jpeg())
    requests = []

    def handle(request):
        requests.append(request)
        assert str(request.url) == "http://docling-ocr:9623/v1/convert/file"
        assert b'name="ocr_preset"\r\n\r\nrapidocr' in request.content
        assert b'name="do_ocr"\r\n\r\ntrue' in request.content
        assert b'name="files"; filename="frame.jpg"' in request.content
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"status": "success", "document": {"text_content": "  Save changes  \n"}},
        )

    settings = mock_ocr_client(monkeypatch, handle)
    result = narration.extract_ocr(frame, lambda: None)

    assert len(requests) == 1
    assert settings["trust_env"] is False
    assert settings["follow_redirects"] is False
    assert result["text"] == "Save changes"
    assert result["provenance"]["provider_version"] == "1.38.2"
    assert result["provenance"]["engine"] == "rapidocr"
    assert result["provenance"]["engine_version"] == "3.10.0"
    assert result["provenance"]["model_sha256"] == narration.OCR_MODEL_SHA256


@pytest.mark.parametrize(("content", "error"), [
    (b"not a jpeg", "ocr_frame_invalid"),
    (b"\xff\xd8\xff" + b"x" * (narration.OCR_MAX_IMAGE_BYTES + 1) + b"\xff\xd9",
     "ocr_frame_invalid"),
    (synthetic_jpeg(width=961), "ocr_frame_dimensions_invalid"),
    (synthetic_jpeg(height=541), "ocr_frame_dimensions_invalid"),
], ids=["not_jpeg", "oversized", "width_invalid", "height_invalid"])
def test_extract_ocr_rejects_invalid_oversized_or_large_frames(
    monkeypatch, tmp_path, content, error,
):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(content)
    mock_ocr_client(
        monkeypatch,
        lambda _request: pytest.fail("Invalid frame must not be submitted"),
    )

    with pytest.raises(narration.NarrationError, match=error):
        narration.extract_ocr(frame, lambda: None)


def test_extract_ocr_bounds_service_response(monkeypatch, tmp_path):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(synthetic_jpeg())
    mock_ocr_client(monkeypatch, lambda _request: httpx.Response(
        200, headers={"content-type": "application/json"},
        content=b"x" * (narration.OCR_MAX_RESPONSE_BYTES + 1),
    ))

    with pytest.raises(narration.NarrationError, match="ocr_response_limit"):
        narration.extract_ocr(frame, lambda: None)


@pytest.mark.parametrize(("document", "error"), [
    ({}, "ocr_service_invalid_response"),
    ({"text_content": None}, "ocr_service_invalid_response"),
    ({"text_content": 123}, "ocr_service_invalid_response"),
])
def test_extract_ocr_rejects_missing_or_invalid_text(monkeypatch, tmp_path, document, error):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(synthetic_jpeg())
    mock_ocr_client(monkeypatch, lambda _request: httpx.Response(
        200, json={"status": "success", "document": document},
    ))
    with pytest.raises(narration.NarrationError, match=error):
        narration.extract_ocr(frame, lambda: None)


def test_extract_ocr_reports_timeout_without_retry(monkeypatch, tmp_path):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(synthetic_jpeg())
    attempts = []

    def timeout(request):
        attempts.append(request)
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    mock_ocr_client(monkeypatch, timeout)
    with pytest.raises(narration.NarrationError, match="ocr_service_timeout"):
        narration.extract_ocr(frame, lambda: None)
    assert len(attempts) == 1


@pytest.mark.parametrize(("ocr_enabled", "vision_enabled"), [
    (True, False),
    (False, True),
    (True, True),
    (False, False),
])
def test_scenes_runs_ocr_and_vision_independently(
    monkeypatch, tmp_path, ocr_enabled, vision_enabled,
):
    def fake_run(args, _check, _timeout=120, env=None, *, capture_stderr=False):
        if str(args[-1]).endswith(".jpg"):
            Path(args[-1]).write_bytes(synthetic_jpeg())
            if capture_stderr:
                return b"", b"[Parsed_showinfo_0] n:0 pts_time:0.000000"
            return b""
        return bytes(64 * 36)

    ocr_calls = []
    vision_calls = []
    monkeypatch.setattr(narration, "run", fake_run)
    monkeypatch.setattr(narration, "extract_ocr", lambda *_args: (
        ocr_calls.append(True) or {
            "text": "Save changes", "provenance": {"engine": "rapidocr"},
        }
    ))
    monkeypatch.setattr(narration, "vision", lambda *_args: (
        vision_calls.append(True) or {"text": "A visible button", "uncertain": True}
    ))
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()

    result = narration.scenes(
        tmp_path / "source.mp4", frames_dir, {"format": {"duration": "1"}},
        narration.NarrationOptions(
            analysis_mode="manual", ocr=ocr_enabled, vision=vision_enabled,
        ), lambda: None,
    )

    assert len(ocr_calls) == int(ocr_enabled)
    assert len(vision_calls) == int(vision_enabled)
    assert result[0]["timestamp_ms"] == 0
    assert result[0]["id"] == "frame-0"
    assert result[0]["ocr"] == ("Save changes" if ocr_enabled else None)
    assert result[0]["ocr_status"] == ("complete" if ocr_enabled else "not_requested")
    assert result[0]["ocr_provenance"] == (
        {"engine": "rapidocr"} if ocr_enabled else None
    )
    assert result[0]["vision"] == (
        {"text": "A visible button", "uncertain": True} if vision_enabled else None
    )
    assert result[0]["vision_status"] == (
        "complete" if vision_enabled else "not_requested"
    )


def test_silent_training_deduplicates_similar_samples_before_ocr(monkeypatch, tmp_path):
    samples = iter([bytes(64 * 36), bytes([1]) * (64 * 36), bytes([30]) * (64 * 36)])

    def fake_run(args, _check, _timeout=120, env=None, *, capture_stderr=False):
        if str(args[-1]).endswith(".jpg"):
            Path(args[-1]).write_bytes(synthetic_jpeg())
            if capture_stderr:
                return b"", b"[Parsed_showinfo_0] n:0 pts_time:0.000000"
            return b""
        return next(samples)

    calls = []
    monkeypatch.setattr(narration, "run", fake_run)
    monkeypatch.setattr(narration, "extract_ocr", lambda *_args: (
        calls.append(True) or {"text": "Visible", "provenance": {}}
    ))
    result = narration.scenes(
        tmp_path / "source.mp4", tmp_path, {"format": {"duration": "30"}},
        narration.NarrationOptions(workflow="silent_training", ocr=True), lambda: None,
    )
    assert [item["timestamp_ms"] for item in result] == [0, 20000]
    assert [item["major_change"] for item in result] == [False, True]
    assert len(calls) == 2


def _test_h264_encoder():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg/FFprobe are required for source-PTS acceptance")
    encoders = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        check=True, capture_output=True, text=True,
    ).stdout
    for candidate in ("libx264", "libopenh264", "h264_mf", "h264_nvenc", "h264_qsv", "h264_amf"):
        if re.search(rf"^\s*V\S*\s+{re.escape(candidate)}\b", encoders, re.MULTILINE):
            return candidate
    pytest.skip("No compatible H.264 encoder available for scene timestamp fixtures")


@pytest.mark.parametrize("kind", ["cfr", "vfr", "offset"])
def test_scenes_timestamp_matches_decoded_source_pts(tmp_path, kind):
    encoder = _test_h264_encoder()

    source = tmp_path / f"{kind}.mp4"
    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10:duration=12",
    ]
    if kind == "vfr":
        command.extend([
            "-vf", "select='not(mod(n,3))+not(mod(n,7))',setpts=PTS-STARTPTS",
            "-fps_mode", "vfr",
        ])
    elif kind == "offset":
        command.extend(["-vf", "setpts=PTS+2/TB"])
    command.extend(["-c:v", encoder])
    if encoder == "libx264":
        command.extend(["-preset", "ultrafast"])
    elif encoder == "libopenh264":
        command.extend(["-b:v", "2M"])
    command.extend([
        "-threads", "1", "-g", "10", "-pix_fmt", "yuv420p", str(source),
    ])
    subprocess.run(command, check=True, capture_output=True)

    def check():
        return None

    metadata = narration.probe(source, check)
    frames = json.loads(narration.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
        "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(source),
    ], check))["frames"]
    source_pts = [float(frame["best_effort_timestamp_time"]) for frame in frames]
    stream_start = float(next(
        stream["start_time"] for stream in metadata["streams"]
        if stream["codec_type"] == "video"
    ))

    frame_directory = tmp_path / "frames"
    frame_directory.mkdir()
    result = narration.scenes(
        source, frame_directory, metadata,
        narration.NarrationOptions(analysis_mode="manual", ocr=False),
        check,
    )

    assert len(result) == 2
    for frame in result:
        source_time = stream_start + frame["timestamp_ms"] / 1000
        assert min(abs(source_time - pts) for pts in source_pts) <= 0.001


def test_scenes_detects_real_transition_and_removes_duplicate_frame(tmp_path):
    encoder = _test_h264_encoder()

    transition = tmp_path / "transition.mp4"
    transition_cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=black:s=160x90:r=5:d=6",
        "-f", "lavfi", "-i", "color=c=white:s=160x90:r=5:d=6",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0,setpts=PTS-STARTPTS[v]",
        "-map", "[v]", "-c:v", encoder,
    ]
    if encoder == "libx264":
        transition_cmd.extend(["-preset", "ultrafast"])
    elif encoder == "libopenh264":
        transition_cmd.extend(["-b:v", "2M"])
    transition_cmd.extend(["-threads", "1", "-g", "5", "-pix_fmt", "yuv420p", str(transition)])
    subprocess.run(transition_cmd, check=True, capture_output=True)

    duplicate = tmp_path / "duplicate.mp4"
    duplicate_cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=black:s=160x90:r=5:d=12",
        "-c:v", encoder,
    ]
    if encoder == "libx264":
        duplicate_cmd.extend(["-preset", "ultrafast"])
    elif encoder == "libopenh264":
        duplicate_cmd.extend(["-b:v", "2M"])
    duplicate_cmd.extend(["-threads", "1", "-g", "5", "-pix_fmt", "yuv420p", str(duplicate)])
    subprocess.run(duplicate_cmd, check=True, capture_output=True)

    def check():
        return None

    options = narration.NarrationOptions(
        workflow="silent_training", analysis_mode="manual", ocr=False,
    )
    transition_directory = tmp_path / "transition-frames"
    transition_directory.mkdir()
    duplicate_directory = tmp_path / "duplicate-frames"
    duplicate_directory.mkdir()
    transition_frames = narration.scenes(
        transition, transition_directory, narration.probe(transition, check),
        options, check,
    )
    duplicate_frames = narration.scenes(
        duplicate, duplicate_directory, narration.probe(duplicate, check),
        options, check,
    )

    assert [frame["timestamp_ms"] for frame in transition_frames] == [0, 6000]
    assert transition_frames[1]["major_change"] is True
    assert [frame["timestamp_ms"] for frame in duplicate_frames] == [0]


def test_probe_rejects_corrupt_video_bytes(tmp_path):
    if not shutil.which("ffprobe"):
        pytest.skip("FFprobe is required to reject corrupt media")
    source = tmp_path / "corrupt.mp4"
    source.write_bytes(b"not a video")

    with pytest.raises(narration.NarrationError, match="media_command_failed"):
        narration.probe(source, lambda: None)


def test_scenes_seeks_from_stream_offset_and_rounds_decoded_pts(monkeypatch, tmp_path):
    pts = iter(["0.000500", "0.200000"])
    pixels = iter([bytes(64 * 36), bytes([30]) * (64 * 36)])
    seeks = []

    def fake_run(args, _check, _timeout=120, env=None, *, capture_stderr=False):
        if str(args[-1]).endswith(".jpg"):
            seeks.append(args[args.index("-ss") + 1])
            Path(args[-1]).write_bytes(synthetic_jpeg())
            if capture_stderr:
                return b"", f"[Parsed_showinfo_0] n:0 pts_time:{next(pts)}".encode()
            return b""
        return next(pixels)

    monkeypatch.setattr(narration, "run", fake_run)
    result = narration.scenes(
        tmp_path / "source.mp4", tmp_path,
        {"format": {"duration": "11"}, "streams": [{
            "codec_type": "video", "duration": "11", "start_time": "2",
        }]},
        narration.NarrationOptions(analysis_mode="manual", ocr=False),
        lambda: None,
    )

    assert seeks == ["2.0", "7.5"]
    assert [frame["timestamp_ms"] for frame in result] == [1, 5700]


def test_scenes_rejects_missing_decoded_pts(monkeypatch, tmp_path):
    def fake_run(args, _check, _timeout=120, env=None, *, capture_stderr=False):
        if capture_stderr:
            return b"", b""
        return b""

    monkeypatch.setattr(narration, "run", fake_run)
    with pytest.raises(narration.NarrationError, match="scene_frame_timestamp_unavailable"):
        narration.scenes(
            tmp_path / "source.mp4", tmp_path,
            {"format": {"duration": "1"}},
            narration.NarrationOptions(analysis_mode="manual", ocr=False),
            lambda: None,
        )


def test_scenes_records_ocr_failure_and_still_runs_vision(monkeypatch, tmp_path):
    def fake_run(args, _check, _timeout=120, env=None, *, capture_stderr=False):
        if str(args[-1]).endswith(".jpg"):
            Path(args[-1]).write_bytes(synthetic_jpeg())
            if capture_stderr:
                return b"", b"[Parsed_showinfo_0] n:0 pts_time:0.000000"
            return b""
        return bytes(64 * 36)

    monkeypatch.setattr(narration, "run", fake_run)
    monkeypatch.setattr(narration, "extract_ocr", lambda *_args: (
        _ for _ in ()
    ).throw(narration.NarrationError("ocr_service_timeout")))
    monkeypatch.setattr(narration, "vision", lambda *_args: {
        "text": "A visible button", "uncertain": True,
    })
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()

    result = narration.scenes(
        tmp_path / "source.mp4", frames_dir, {"format": {"duration": "1"}},
        narration.NarrationOptions(analysis_mode="manual", vision=True), lambda: None,
    )

    assert result[0]["ocr"] is None
    assert result[0]["ocr_status"] == "unavailable"
    assert result[0]["ocr_error"] == "ocr_service_timeout"
    assert result[0]["vision"]["text"] == "A visible button"
    assert result[0]["vision_status"] == "complete"


@pytest.mark.parametrize(("ocr_text", "ocr_status"), [
    ("", "no_text"),
    ("Synthetic title", "complete"),
])
def test_scenes_keeps_ocr_when_vision_fails(monkeypatch, tmp_path, ocr_text, ocr_status):
    def fake_run(args, _check, _timeout=120, env=None, *, capture_stderr=False):
        if str(args[-1]).endswith(".jpg"):
            Path(args[-1]).write_bytes(synthetic_jpeg())
            if capture_stderr:
                return b"", b"[Parsed_showinfo_0] n:0 pts_time:0.000000"
            return b""
        return bytes(64 * 36)

    monkeypatch.setattr(narration, "run", fake_run)
    monkeypatch.setattr(narration, "extract_ocr", lambda *_args: {
        "text": ocr_text, "provenance": {"engine": "rapidocr"},
    })
    monkeypatch.setattr(narration, "vision", lambda *_args: (
        _ for _ in ()
    ).throw(httpx.ReadTimeout("sensitive provider detail")))
    result = narration.scenes(
        tmp_path / "source.mp4", tmp_path, {"format": {"duration": "1"}},
        narration.NarrationOptions(analysis_mode="manual", vision=True), lambda: None,
    )
    assert result[0]["ocr"] == ocr_text
    assert result[0]["ocr_status"] == ocr_status
    assert result[0]["vision"] is None
    assert result[0]["vision_status"] == "unavailable"
    assert result[0]["vision_error"] == "vision_provider_request_or_response_failed"


def test_scenes_does_not_swallow_cancellation_after_provider_failure(monkeypatch, tmp_path):
    def fake_run(args, _check, _timeout=120, env=None, *, capture_stderr=False):
        if str(args[-1]).endswith(".jpg"):
            Path(args[-1]).write_bytes(synthetic_jpeg())
            if capture_stderr:
                return b"", b"[Parsed_showinfo_0] n:0 pts_time:0.000000"
            return b""
        return bytes(64 * 36)

    checks = 0

    def check():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise CancellationRequestedError()

    monkeypatch.setattr(narration, "run", fake_run)
    monkeypatch.setattr(narration, "extract_ocr", lambda *_args: (
        _ for _ in ()
    ).throw(narration.NarrationError("ocr_service_timeout")))
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()

    with pytest.raises(CancellationRequestedError):
        narration.scenes(
            tmp_path / "source.mp4", frames_dir, {"format": {"duration": "1"}},
            narration.NarrationOptions(analysis_mode="manual"), check,
        )


def test_narration_job_identity_mismatch_rejected(tmp_path):
    """A job whose narration row.job_id does not match is rejected."""
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)

    actor = Actor(name="test-owner", user_id="user-1",
                  roles=["viewer", "producer", "reviewer", "administrator"],
                  session_id="session-1")

    with sessions() as db:
        project = Project(id="proj-1", name="Test Project", owner=actor.name)
        db.add(project)
        membership = ProjectMembership(
            project_id="proj-1", user_id=actor.user_id, role="owner",
        )
        db.add(membership)
        user = User(id=actor.user_id, username="owner", password_hash="x")
        db.add(user)
        asset = MediaAsset(
            id="media-1", project_id="proj-1", filename="test.mp4",
            media_type="video/mp4", size_bytes=1000, sha256="abc123",
            storage_key="source/video.mp4",
        )
        db.add(asset)
        db.flush()
        # Create both jobs first (job_id is NOT NULL on narrations)
        job_a = Job(
            id="job-a", owner=actor.name, owner_principal_kind="user",
            owner_principal_id=actor.user_id, project_id="proj-1",
            media_id="media-1", operation="narrate_existing_video",
            status="dispatch_pending",
            spec={"narration_id": "narr-1", "phase": "analysis", "revision": 1,
                  "attempt": 0},
        )
        db.add(job_a)
        job_b = Job(
            id="job-b", owner=actor.name, owner_principal_kind="user",
            owner_principal_id=actor.user_id, project_id="proj-1",
            media_id="media-1", operation="narrate_existing_video",
            status="dispatch_pending",
            spec={"narration_id": "narr-1", "phase": "render", "revision": 1,
                  "attempt": 0},
        )
        db.add(job_b)
        db.flush()
        # Narration's job_id points to job_a
        row = Narration(
            id="narr-1", project_id="proj-1", media_id="media-1",
            source_sha256="digest-of-source", stage="queued", revision=1,
            job_id="job-a",
            options={"workflow": "silent_training"},
        )
        db.add(row)
        db.commit()

    # Job B (id=job-b) tries to execute, but row.job_id=job-a → mismatch
    with sessions() as db:
        with pytest.raises(narration.NarrationError,
                          match="narration_job_identity_mismatch"):
            narration_jobs.execute(db, db.get(Job, "job-b"), lambda: None)

    engine.dispose()


def test_narration_job_identity_valid_advances(tmp_path, monkeypatch):
    """A job whose narration row.job_id matches advances correctly."""
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)

    actor = Actor(name="test-owner", user_id="user-1",
                  roles=["viewer", "producer", "reviewer", "administrator"],
                  session_id="session-1")

    with sessions() as db:
        project = Project(id="proj-1", name="Test Project", owner=actor.name)
        db.add(project)
        membership = ProjectMembership(
            project_id="proj-1", user_id=actor.user_id, role="owner",
        )
        db.add(membership)
        user = User(id=actor.user_id, username="owner", password_hash="x")
        db.add(user)
        asset = MediaAsset(
            id="media-1", project_id="proj-1", filename="test.mp4",
            media_type="video/mp4", size_bytes=1000, sha256="abc123",
            storage_key="source/video.mp4",
        )
        db.add(asset)
        db.flush()
        job = Job(
            id="job-1", owner=actor.name, owner_principal_kind="user",
            owner_principal_id=actor.user_id, project_id="proj-1",
            media_id="media-1", operation="narrate_existing_video",
            status="dispatch_pending",
            spec={"narration_id": "narr-1", "phase": "analysis", "revision": 1,
                  "attempt": 0},
        )
        db.add(job)
        db.flush()
        source_bytes = b"synthetic-video-bytes"
        source_digest = hashlib.sha256(source_bytes).hexdigest()
        # Narration's job_id matches the job
        row = Narration(
            id="narr-1", project_id="proj-1", media_id="media-1",
            source_sha256=source_digest, stage="queued", revision=1,
            job_id="job-1",
            options={"workflow": "silent_training"},
        )
        db.add(row)
        db.commit()

    monkeypatch.setattr(narration_jobs, "ARTIFACT_ROOT", tmp_path)
    monkeypatch.setattr(narration, "probe", lambda *_args: {
        "format": {"duration": "90"}, "streams": [{"codec_type": "video"}],
    })
    def synthetic_scenes(_source, directory, _m, _o, _c):
        frame_path = directory / "frame-0.jpg"
        frame_path.write_bytes(synthetic_jpeg())
        frame_digest = hashlib.sha256(frame_path.read_bytes()).hexdigest()
        return [{"id": "frame-0", "timestamp_ms": 0,
                 "frame_sha256": frame_digest,
                 "ocr_status": "complete",
                 "vision_status": "not_requested", "ocr": "Menu"}]
    monkeypatch.setattr(narration, "scenes", synthetic_scenes)
    monkeypatch.setattr(narration, "transcribe", lambda *_args: (
        AssertionError("Silent training must not run Whisper")
    ))

    with sessions() as db:
        asset = db.get(MediaAsset, "media-1")
        source_bytes = b"synthetic-video-bytes"
        source_digest = hashlib.sha256(source_bytes).hexdigest()
        asset.sha256 = source_digest
        asset.size_bytes = len(source_bytes)
        narration_jobs.write_private_file(tmp_path, "source/video.mp4",
                                          source_bytes)
        db.commit()
        # Valid job: row.job_id == job.id → should not raise identity mismatch
        narration_jobs.execute(db, db.get(Job, "job-1"), lambda: None)

    engine.dispose()
