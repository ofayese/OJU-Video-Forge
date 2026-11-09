import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import api, narration
from app.auth import Actor, get_current_actor
from app.core import (
    Base, DispatchOutbox, Job, MediaAsset, Narration, NarrationRevision,
    Project, ProjectMembership, User, WorkflowArtifact,
)


@pytest.fixture
def context(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)

    def database():
        with sessions() as db:
            yield db

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
                          storage_key="synthetic.mp4"))
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
