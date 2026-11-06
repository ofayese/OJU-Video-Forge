import asyncio
from contextlib import contextmanager
from pathlib import Path

import pytest
from redis import Redis
from rq.job import Job as RQJob
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import api, core, worker
from app.core import Audit, Base, Job


class FakeRQJob:
    def __init__(self, status):
        self.status = status

    def get_status(self, refresh=False):
        return self.status


class FakeQueue:
    def __init__(self, jobs=None):
        self.jobs = jobs or {}
        self.enqueued = []

    def fetch_job(self, job_id):
        return self.jobs.get(job_id)

    def enqueue(self, function, job_id_arg, *, job_id, job_timeout):
        self.enqueued.append((function, job_id_arg, job_id, job_timeout))
        self.jobs[job_id] = FakeRQJob("queued")


@pytest.fixture
def database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'resilience.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        yield db
    engine.dispose()


def add_job(db, *, status, recovery_count=0, job_id="job-1", operation="render_test"):
    job = Job(
        id=job_id,
        owner="synthetic-owner",
        operation=operation,
        status=status,
        spec={"prompt": "private synthetic prompt"},
        recovery_count=recovery_count,
    )
    db.add(job)
    db.commit()
    return job


def test_readiness_requires_only_local_database_and_redis(monkeypatch):
    from fastapi.testclient import TestClient

    class Connection:
        closed = False

        async def execute(self, statement):
            assert statement == "SELECT 1"

        async def close(self):
            self.closed = True

    connection = Connection()

    class AsyncConnection:
        @staticmethod
        async def connect(url, *, connect_timeout, options):
            assert url.startswith("postgresql://")
            assert connect_timeout == 1
            assert options == "-c statement_timeout=1000"
            return connection

    class RedisConnection:
        def ping(self):
            return True

        def close(self):
            pass

    monkeypatch.setattr(api, "AsyncConnection", AsyncConnection)
    monkeypatch.setattr(api.Redis, "from_url", lambda *args, **kwargs: RedisConnection())
    response = TestClient(api.app).get("/health/ready")

    assert response.status_code == 200
    assert connection.closed
    assert response.json() == {
        "ready": True,
        "required": {"database": True, "redis": True},
        "optional": {"gpu": "not_required", "cloud": "not_required"},
    }


def test_readiness_fails_when_required_dependency_is_down(monkeypatch):
    from fastapi.testclient import TestClient

    class BrokenConnection:
        @staticmethod
        async def connect(*args, **kwargs):
            raise OSError("private connection detail")

    monkeypatch.setattr(api, "AsyncConnection", BrokenConnection)
    monkeypatch.setattr(api.Redis, "from_url", lambda *args, **kwargs: object())
    response = TestClient(api.app).get("/health/ready")

    assert response.status_code == 503
    assert "private connection detail" not in response.text
    assert response.json()["optional"]["gpu"] == "not_required"


def test_readiness_closes_connection_when_database_probe_times_out(monkeypatch):
    from fastapi.testclient import TestClient

    class StalledConnection:
        closed = False

        async def execute(self, statement):
            assert statement == "SELECT 1"
            await asyncio.sleep(10)

        async def close(self):
            self.closed = True

    connection = StalledConnection()

    class AsyncConnection:
        @staticmethod
        async def connect(*args, **kwargs):
            return connection

    monkeypatch.setattr(api, "AsyncConnection", AsyncConnection)
    monkeypatch.setattr(api, "DATABASE_PROBE_TIMEOUT_SECONDS", 0.01)

    class RedisConnection:
        def ping(self):
            return True

        def close(self):
            pass

    monkeypatch.setattr(api.Redis, "from_url", lambda *args, **kwargs: RedisConnection())
    response = TestClient(api.app).get("/health/ready")

    assert response.status_code == 503
    assert connection.closed


def test_recovery_requeues_interrupted_job_once(database):
    job = add_job(database, status="running")
    queue = FakeQueue()

    assert core.recover_jobs(database, queue) == 1
    assert job.status == "dispatch_pending"
    assert job.recovery_count == 1
    assert queue.enqueued == [
        ("app.worker.execute", job.id, "job-1-recovery-1", 3600)
    ]
    assert RQJob(
        id=queue.enqueued[0][2],
        connection=Redis.from_url("redis://localhost:6379/0"),
    ).id == "job-1-recovery-1"
    assert core.recover_jobs(database, queue) == 0
    assert database.query(Audit).filter_by(action="interrupted_recovery").count() == 1


def test_recovery_dispatches_persisted_pending_job(database):
    job = add_job(database, status="dispatch_pending")
    queue = FakeQueue()

    assert core.recover_jobs(database, queue) == 1
    assert job.recovery_count == 0
    assert queue.enqueued == [("app.worker.execute", job.id, job.id, 3600)]


def test_recovery_requeues_running_job_with_stale_started_delivery(database):
    job = add_job(database, status="running")
    queue = FakeQueue({job.id: FakeRQJob("started")})

    assert core.recover_jobs(database, queue) == 1
    assert job.recovery_count == 1
    assert queue.enqueued == [
        ("app.worker.execute", job.id, "job-1-recovery-1", 3600)
    ]


def test_recovery_requeues_started_delivery_before_running_transition(database):
    job = add_job(database, status="queued")
    queue = FakeQueue({job.id: FakeRQJob("started")})

    assert core.recover_jobs(database, queue) == 1
    assert job.status == "dispatch_pending"
    assert job.recovery_count == 1
    assert queue.enqueued == [
        ("app.worker.execute", job.id, "job-1-recovery-1", 3600)
    ]


def test_recovery_does_not_duplicate_queued_delivery_or_restart_terminal_job(database):
    queued = add_job(database, status="queued")
    terminal = add_job(database, status="awaiting_review", job_id="job-2")
    queue = FakeQueue({queued.id: FakeRQJob("queued")})

    assert core.recover_jobs(database, queue) == 0
    assert queued.status == "queued"
    assert terminal.status == "awaiting_review"
    assert queue.enqueued == []


def test_recovery_exhaustion_fails_without_exposing_prompt(database):
    job = add_job(database, status="running", recovery_count=1)
    queue = FakeQueue()

    assert core.recover_jobs(database, queue) == 0
    assert job.status == "failed"
    assert job.error == "Interrupted job recovery limit exceeded"
    assert "private synthetic prompt" not in job.error
    assert queue.enqueued == []


def test_recovery_does_not_repeat_non_idempotent_operation(database):
    job = add_job(database, status="running", operation="wan22_t2v")
    queue = FakeQueue()

    assert core.recover_jobs(database, queue) == 0
    assert job.status == "failed"
    assert job.error == "Interrupted operation requires explicit resubmission"
    assert queue.enqueued == []


def test_restored_job_owner_boundary_remains_enforced(monkeypatch, database):
    from fastapi.testclient import TestClient

    from app.auth import Actor, get_current_actor

    factory = sessionmaker(database.get_bind(), expire_on_commit=False)
    job = add_job(database, status="awaiting_review")

    def synthetic_session():
        with factory() as session:
            yield session

    monkeypatch.setitem(api.app.dependency_overrides, api.db_session, synthetic_session)
    actor = Actor(name="synthetic-owner", roles=["viewer"])
    monkeypatch.setitem(api.app.dependency_overrides, get_current_actor, lambda: actor)
    client = TestClient(api.app)

    assert client.get(f"/v1/jobs/{job.id}").status_code == 200
    actor.name = "different-synthetic-owner"
    assert client.get(f"/v1/jobs/{job.id}").status_code == 404


def test_worker_failure_persists_only_sanitized_error(monkeypatch, database, tmp_path):
    factory = sessionmaker(database.get_bind(), expire_on_commit=False)
    job = add_job(database, status="queued", operation="unsupported")

    @contextmanager
    def session_context():
        with factory() as session:
            yield session

    monkeypatch.setattr(worker, "SessionLocal", session_context)
    monkeypatch.setattr(worker, "ARTIFACT_ROOT", tmp_path)

    with pytest.raises(RuntimeError, match="job execution failed"):
        worker.execute(job.id)

    database.refresh(job)
    assert job.status == "failed"
    assert job.error == "Job execution failed; details withheld"
    assert "private synthetic prompt" not in job.error


def test_interrupted_render_can_reuse_its_private_output_directory(
    monkeypatch, database, tmp_path
):
    import hashlib

    factory = sessionmaker(database.get_bind(), expire_on_commit=False)
    job = add_job(database, status="dispatch_pending")
    output_dir = tmp_path / "artifacts" / job.id
    output_dir.mkdir(parents=True)
    protected_video = tmp_path / "protected-video"
    protected_video.write_bytes(b"unrelated video data")
    (output_dir / "test.mp4").symlink_to(protected_video)
    protected_checksum = tmp_path / "protected-checksum"
    protected_checksum.write_text("unrelated checksum data")
    (output_dir / "SHA256SUMS").symlink_to(protected_checksum)

    @contextmanager
    def session_context():
        with factory() as session:
            yield session

    def fake_ffmpeg(args, **kwargs):
        Path(args[-1]).write_bytes(b"complete synthetic output")

    monkeypatch.setattr(worker, "SessionLocal", session_context)
    monkeypatch.setattr(worker, "ARTIFACT_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(worker.subprocess, "run", fake_ffmpeg)

    worker.execute(job.id)

    database.refresh(job)
    output = output_dir / "test.mp4"
    assert job.status == "awaiting_review"
    assert not output.is_symlink()
    assert output.read_bytes() == b"complete synthetic output"
    checksum = output_dir / "SHA256SUMS"
    assert not checksum.is_symlink()
    assert checksum.read_text() == (
        f"{hashlib.sha256(output.read_bytes()).hexdigest()}  test.mp4\n"
    )
    assert protected_video.read_bytes() == b"unrelated video data"
    assert protected_checksum.read_text() == "unrelated checksum data"


def test_worker_hides_error_when_failure_state_cannot_be_saved(monkeypatch, database):
    factory = sessionmaker(database.get_bind(), expire_on_commit=False)
    job = add_job(database, status="queued", operation="unsupported")

    @contextmanager
    def session_context():
        with factory() as session:
            yield session

    def fail_transition(db, job, status, actor, **kwargs):
        if status != "running":
            raise ValueError("private synthetic prompt")

    monkeypatch.setattr(worker, "SessionLocal", session_context)
    monkeypatch.setattr(worker, "transition", fail_transition)

    with pytest.raises(RuntimeError, match="job execution failed") as error:
        worker.execute(job.id)

    assert error.value.__cause__ is None
    assert error.value.__suppress_context__
    assert "private synthetic prompt" not in str(error.value)
