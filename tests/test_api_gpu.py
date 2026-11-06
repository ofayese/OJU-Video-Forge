import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import api
from app.auth import Actor, get_current_actor
from app.config import Settings
from app.core import Audit, Base, DispatchOutbox, Job


@pytest.fixture
def api_context(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)

    def db_override():
        with sessions() as db:
            yield db

    def set_actor(name):
        api.app.dependency_overrides[get_current_actor] = lambda: Actor(
            name=name,
            roles=["viewer", "producer", "reviewer", "administrator", "agent_service"],
        )

    api.app.dependency_overrides[api.db_session] = db_override
    set_actor("owner")
    monkeypatch.setattr(api, "ARTIFACT_ROOT", tmp_path)
    monkeypatch.setattr(api, "get_settings", lambda: Settings(comfyui_enabled=True))
    with TestClient(api.app) as client:
        yield client, sessions, set_actor, tmp_path
    api.app.dependency_overrides.clear()
    engine.dispose()


def test_artifact_download_is_owner_only_audited_and_hides_storage_key(api_context):
    client, sessions, set_actor, root = api_context
    job_id = "owned-job"
    job_dir = root / job_id
    job_dir.mkdir()
    (job_dir / "generated.mp4").write_bytes(b"synthetic MP4")
    with sessions() as db:
        db.add(
            Job(
                id=job_id,
                owner="owner",
                operation="wan22_t2v",
                status="awaiting_review",
                spec={},
                artifact=f"{job_id}/generated.mp4",
            )
        )
        db.commit()

    status = client.get(f"/v1/jobs/{job_id}")
    assert status.status_code == 200
    assert status.json()["artifact"] == f"/v1/jobs/{job_id}/artifact"
    assert f"{job_id}/generated.mp4" not in status.text

    response = client.get(f"/v1/jobs/{job_id}/artifact")
    assert response.status_code == 200
    assert response.content == b"synthetic MP4"
    assert response.headers["content-disposition"] == 'attachment; filename="video.mp4"'
    with sessions() as db:
        assert db.query(Audit).filter_by(
            job_id=job_id, actor="owner", action="artifact_downloaded"
        ).count() == 1

    set_actor("other")
    assert client.get(f"/v1/jobs/{job_id}/artifact").status_code == 404


def test_retry_creates_audited_job_and_dispatches_from_outbox(api_context, monkeypatch):
    client, sessions, _set_actor, _root = api_context
    original_id = "failed-job"
    with sessions() as db:
        db.add(
            Job(
                id=original_id,
                owner="owner",
                operation="render_test",
                status="failed",
                spec={"operation": "render_test"},
            )
        )
        db.commit()

    enqueued = []

    class QueueStub:
        def __init__(self, name):
            self.name = name

        def fetch_job(self, _job_id):
            return None

        def enqueue(self, *args, **kwargs):
            enqueued.append((self.name, args, kwargs))

    monkeypatch.setattr("app.dispatcher.SessionLocal", sessions)
    monkeypatch.setattr("app.dispatcher.queue", lambda name="video": QueueStub(name))
    response = client.post(f"/v1/jobs/{original_id}/retry")

    assert response.status_code == 202
    retried_id = response.json()["id"]
    assert retried_id != original_id
    assert enqueued[0][0] == "video"
    with sessions() as db:
        assert db.get(Job, retried_id).status == "queued"
        assert db.get(DispatchOutbox, retried_id).status == "dispatched"
        assert db.query(Audit).filter_by(
            job_id=original_id, action="retry_requested"
        ).count() == 1


def test_gpu_job_is_persisted_before_gpu_queue_dispatch(api_context, monkeypatch):
    client, sessions, _set_actor, _root = api_context
    dispatches = []

    class QueueStub:
        def __init__(self, name):
            self.name = name

        def fetch_job(self, _job_id):
            return None

        def enqueue(self, function, *args, **kwargs):
            job_id = kwargs["job_id"]
            assert args == (job_id,)
            with sessions() as db:
                job = db.get(Job, job_id)
                assert job is not None
                assert job.status == "dispatch_pending"
                assert job.operation == "wan22_t2v"
                assert db.get(DispatchOutbox, job_id).status == "dispatching"
            dispatches.append((self.name, function, args, kwargs))

    monkeypatch.setattr("app.dispatcher.SessionLocal", sessions)
    monkeypatch.setattr(
        "app.dispatcher.queue", lambda name="video": QueueStub(name)
    )
    response = client.post(
        "/v1/jobs",
        json={"operation": "wan22_t2v", "prompt": "synthetic prompt"},
    )

    assert response.status_code == 202
    assert dispatches[0][0] == "gpu"
    assert dispatches[0][1] == "app.worker.execute"
    assert dispatches[0][3]["job_timeout"] == 7200
    with sessions() as db:
        job_id = response.json()["id"]
        assert db.get(Job, job_id).status == "queued"
        assert db.get(DispatchOutbox, job_id).status == "dispatched"


def test_retry_limit_is_three_audited_attempts(api_context):
    client, sessions, _set_actor, _root = api_context
    original_id = "exhausted-job"
    with sessions() as db:
        db.add(
            Job(
                id=original_id,
                owner="owner",
                operation="render_test",
                status="failed",
                spec={"operation": "render_test"},
            )
        )
        db.add_all(
            [
                Audit(job_id=original_id, actor="owner", action="retry_requested")
                for _ in range(3)
            ]
        )
        db.commit()

    assert client.post(f"/v1/jobs/{original_id}/retry").status_code == 409


def test_generation_is_disabled_by_default(api_context, monkeypatch):
    client, _sessions, _set_actor, _root = api_context
    monkeypatch.setattr(api, "get_settings", lambda: Settings(comfyui_enabled=False))

    response = client.post(
        "/v1/jobs",
        json={"operation": "wan22_t2v", "prompt": "synthetic prompt"},
    )

    assert response.status_code == 503


def test_openapi_documents_authenticated_mp4_download(api_context):
    _client, _sessions, _set_actor, _root = api_context
    schema = api.app.openapi()

    assert schema["paths"]["/v1/jobs/{job_id}/artifact"]["get"]["operationId"] == "getVideoArtifact"
    assert "video/mp4" in schema["paths"]["/v1/jobs/{job_id}/artifact"]["get"]["responses"]["200"]["content"]
