import hashlib
import json
import stat
import wave
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import api, worker
from app.auth import Actor, get_current_actor
from app.core import AudioAsset, Base, Job, WorkflowArtifact


def synthetic_wav():
    output = BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 16000)
    return output.getvalue()


class QueueStub:
    def __init__(self):
        self.job_ids = []
        self.available = True

    def enqueue(self, _function, *args, **kwargs):
        if not self.available:
            raise RuntimeError("queue unavailable")
        self.job_ids.append(kwargs["job_id"])


@pytest.fixture
def workflow_app(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    actor = {"name": "alice"}
    queue = QueueStub()
    root = tmp_path / "private"
    monkeypatch.setattr(api, "ARTIFACT_ROOT", root)
    monkeypatch.setattr(worker, "ARTIFACT_ROOT", root)
    monkeypatch.setattr(api, "queue", lambda: queue)
    monkeypatch.setattr(worker, "SessionLocal", sessions)

    def session_override():
        with sessions() as db:
            yield db

    api.app.dependency_overrides[api.db_session] = session_override
    api.app.dependency_overrides[get_current_actor] = (
        lambda: Actor(name=actor["name"], roles=["viewer", "producer", "administrator"])
    )
    with TestClient(api.app) as client:
        yield client, sessions, actor, queue, root
    api.app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def create_project(client):
    response = client.post("/v1/projects", json={"name": "Speech project"})
    assert response.status_code == 201
    return response.json()["id"]


def upload_audio(client, project_id):
    response = client.post(
        f"/v1/projects/{project_id}/audio",
        files={"upload": ("untrusted-name.wav", synthetic_wav(), "audio/wav")},
    )
    assert response.status_code == 201
    return response.json()


def test_project_audio_and_caption_workflow_are_private_and_manifested(workflow_app):
    client, sessions, actor, queue, root = workflow_app
    project_id = create_project(client)
    uploaded = upload_audio(client, project_id)
    assert uploaded["sha256"] == hashlib.sha256(synthetic_wav()).hexdigest()
    assert "private" not in json.dumps(uploaded)

    with sessions() as db:
        asset = db.get(AudioAsset, uploaded["id"])
        stored_audio = root / asset.relative_path
        assert stored_audio.read_bytes() == synthetic_wav()
        assert stat.S_IMODE(stored_audio.stat().st_mode) == 0o600

    payload = {
        "workflow_version": 1,
        "step": "captions",
        "audio_asset_id": uploaded["id"],
        "captions": [{"start_ms": 0, "end_ms": 1000, "text": "Synthetic caption"}],
    }
    submitted = client.post(f"/v1/projects/{project_id}/workflows", json=payload)
    assert submitted.status_code == 202
    job_id = submitted.json()["id"]
    assert queue.job_ids == [job_id]

    worker.execute(job_id)
    status_response = client.get(f"/v1/projects/{project_id}/workflows/{job_id}")
    assert status_response.json()["status"] == "awaiting_review"
    manifest_response = client.get(
        f"/v1/projects/{project_id}/artifacts/{status_response.json()['artifact']}"
    )
    assert manifest_response.status_code == 200
    document = manifest_response.json()
    assert document["project_id"] == project_id
    assert document["job_id"] == job_id
    assert document["step"] == "captions"
    assert document["inputs"][0]["sha256"] == uploaded["sha256"]
    assert document["inputs"][1]["kind"] == "caption_cues"
    srt_id = document["outputs"]["captions.srt"]["artifact_id"]
    srt_response = client.get(f"/v1/projects/{project_id}/artifacts/{srt_id}")
    assert srt_response.content == b"1\n00:00:00,000 --> 00:00:01,000\nSynthetic caption\n"
    assert hashlib.sha256(srt_response.content).hexdigest() == document["outputs"]["captions.srt"]["sha256"]
    with sessions() as db:
        assert db.query(WorkflowArtifact).filter_by(job_id=job_id).count() == 3
        assert db.get(Job, job_id).artifact == status_response.json()["artifact"]
    worker.execute(job_id)
    with sessions() as db:
        assert db.query(WorkflowArtifact).filter_by(job_id=job_id).count() == 3

    actor["name"] = "bob"
    assert client.get(f"/v1/projects/{project_id}").status_code == 404
    assert client.get(f"/v1/projects/{project_id}/artifacts/{srt_id}").status_code == 404
    assert client.get(f"/v1/projects/{project_id}/audio/{uploaded['id']}").status_code == 404


def test_model_steps_fail_explicitly_and_can_be_retried(workflow_app):
    client, sessions, _actor, queue, _root = workflow_app
    project_id = create_project(client)
    uploaded = upload_audio(client, project_id)
    submitted = client.post(
        f"/v1/projects/{project_id}/workflows",
        json={"workflow_version": 1, "step": "transcription",
              "audio_asset_id": uploaded["id"]},
    )
    assert submitted.status_code == 202
    job_id = submitted.json()["id"]
    with pytest.raises(worker.ModelProviderUnavailable):
        worker.execute(job_id)
    failed = client.get(f"/v1/projects/{project_id}/workflows/{job_id}").json()
    assert failed["status"] == "failed"
    assert failed["error"] == "provider_unavailable; NOT_VERIFIED"

    retry = client.post(f"/v1/projects/{project_id}/workflows/{job_id}/retry")
    assert retry.status_code == 202
    retry_id = retry.json()["id"]
    assert queue.job_ids == [job_id, retry_id]
    assert client.post(f"/v1/projects/{project_id}/workflows/{job_id}/retry").status_code == 409
    with sessions() as db:
        job = db.get(Job, retry_id)
        assert job.retry_of_id == job_id
        assert job.spec["attempt"] == 2
    assert client.post(f"/v1/projects/{project_id}/workflows/{retry_id}/retry").status_code == 409
    with pytest.raises(worker.ModelProviderUnavailable):
        worker.execute(retry_id)
    retry2 = client.post(f"/v1/projects/{project_id}/workflows/{retry_id}/retry")
    assert retry2.status_code == 202
    retry2_id = retry2.json()["id"]
    with sessions() as db:
        assert db.get(Job, retry2_id).spec["attempt"] == 3
    with pytest.raises(worker.ModelProviderUnavailable):
        worker.execute(retry2_id)
    assert client.post(f"/v1/projects/{project_id}/workflows/{retry2_id}/retry").status_code == 409


def test_audio_upload_rejects_non_wav_and_invalid_captions(workflow_app):
    client, _sessions, _actor, _queue, _root = workflow_app
    project_id = create_project(client)
    invalid_audio = client.post(
        f"/v1/projects/{project_id}/audio",
        files={"upload": ("bad.wav", b"not a wav", "audio/wav")},
    )
    assert invalid_audio.status_code == 422
    invalid_captions = client.post(
        f"/v1/projects/{project_id}/workflows",
        json={"workflow_version": 1, "step": "captions",
              "captions": [{"start_ms": 20, "end_ms": 10, "text": "bad"}]},
    )
    assert invalid_captions.status_code == 422
    missing_version = client.post(
        f"/v1/projects/{project_id}/workflows",
        json={"step": "captions", "captions": []},
    )
    assert missing_version.status_code == 422


def test_narration_step_is_versioned_and_marks_missing_provider(workflow_app):
    client, sessions, _actor, _queue, _root = workflow_app
    project_id = create_project(client)
    submitted = client.post(
        f"/v1/projects/{project_id}/workflows",
        json={"workflow_version": 1, "step": "narration",
              "text": "Synthetic narration", "language": "en"},
    )
    assert submitted.status_code == 202
    job_id = submitted.json()["id"]
    with sessions() as db:
        job = db.get(Job, job_id)
        assert job.spec["workflow_version"] == 1
        assert job.spec["input_manifest"][0]["kind"] == "narration_text"
        assert job.spec["input_manifest"][0]["sha256"] == hashlib.sha256(
            b"Synthetic narration").hexdigest()
    with pytest.raises(worker.ModelProviderUnavailable):
        worker.execute(job_id)
    result = client.get(f"/v1/projects/{project_id}/workflows/{job_id}").json()
    assert result["status"] == "failed"
    assert result["error"] == "provider_unavailable; NOT_VERIFIED"


def test_dispatch_pending_workflow_can_be_reenqueued(workflow_app):
    client, sessions, _actor, queue, _root = workflow_app
    project_id = create_project(client)
    queue.available = False
    submitted = client.post(
        f"/v1/projects/{project_id}/workflows",
        json={"workflow_version": 1, "step": "captions", "captions": []},
    )
    assert submitted.status_code == 202
    job_id = submitted.json()["id"]
    assert submitted.json()["status"] == "dispatch_pending"
    assert client.post(
        f"/v1/projects/{project_id}/workflows/{job_id}/retry"
    ).status_code == 503
    assert client.get(
        f"/v1/projects/{project_id}/workflows/{job_id}"
    ).json()["status"] == "dispatch_pending"

    queue.available = True
    dispatched = client.post(
        f"/v1/projects/{project_id}/workflows/{job_id}/retry"
    )
    assert dispatched.status_code == 202
    assert dispatched.json()["id"] == job_id
    assert dispatched.json()["status"] == "queued"
    assert queue.job_ids == [job_id]
    assert client.post(
        f"/v1/projects/{project_id}/workflows/{job_id}/retry"
    ).status_code == 409
    with sessions() as db:
        assert db.get(Job, job_id).spec["attempt"] == 1
