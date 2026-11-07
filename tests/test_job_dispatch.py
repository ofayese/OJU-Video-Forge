from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app import api
from app.auth import Actor, get_current_actor
from app.core import (
    Base,
    DispatchOutbox,
    Job,
    MediaAsset,
    Project,
    REDIS_URL,
    SessionLocal,
    engine,
    transition,
)
from app.dispatcher import (
    STALE_WORKER_SECONDS,
    dispatch_outbox,
    recover_undelivered_jobs,
    recover_stale_jobs,
)


def _isolated_service_urls() -> bool:
    if os.getenv("APP_ENV") != "test":
        return False
    try:
        db_url = make_url(os.environ["DATABASE_URL"])
        redis_url = urlparse(os.environ["REDIS_URL"])
    except (KeyError, ValueError):
        return False
    return (
        bool(db_url.database)
        and db_url.database.endswith("_test")
        and db_url.host in {"localhost", "127.0.0.1", "postgres"}
        and redis_url.hostname in {"localhost", "127.0.0.1", "redis"}
        and redis_url.path == "/15"
    )


pytestmark = pytest.mark.skipif(
    not _isolated_service_urls(),
    reason=(
        "job dispatch integration tests require APP_ENV=test and disposable "
        "*_test PostgreSQL plus Redis database 15"
    ),
)


@pytest.fixture(autouse=True)
def isolated_database_and_redis():
    assert _isolated_service_urls()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    redis = Redis.from_url(REDIS_URL)
    redis.flushdb()
    yield
    redis.flushdb()
    Base.metadata.drop_all(engine)


@pytest.fixture
def api_client():
    api.app.dependency_overrides[get_current_actor] = lambda: Actor(
        name="test-producer", roles=["producer"]
    )
    try:
        with TestClient(api.app) as client:
            yield client
    finally:
        api.app.dependency_overrides.pop(get_current_actor, None)


def _new_job(status="dispatch_pending"):
    job_id = str(uuid.uuid4())
    with SessionLocal() as db:
        db.add(
            Job(
                id=job_id,
                owner="test-producer",
                operation="render_test",
                status=status,
                spec={"operation": "render_test"},
            )
        )
        db.flush()
        db.add(
            DispatchOutbox(
                job_id=job_id,
                status="dispatched" if status == "running" else "pending",
            )
        )
        db.commit()
    return job_id


def test_submission_commits_job_and_outbox_before_dispatch_and_retries(monkeypatch):
    job_id = _new_job()

    class UnavailableQueue:
        def fetch_job(self, _job_id):
            return None

        def enqueue(self, *_args, **_kwargs):
            with SessionLocal() as db:
                assert db.get(Job, job_id) is not None
                assert db.get(DispatchOutbox, job_id) is not None
            raise ConnectionError("redis unavailable")

    monkeypatch.setattr("app.dispatcher.queue", UnavailableQueue)
    assert dispatch_outbox(job_id) is False
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        outbox = db.get(DispatchOutbox, job_id)
        assert job.status == "dispatch_pending"
        assert job.dispatch_attempts == 1
        assert job.dispatch_error == "ConnectionError"
        assert outbox.status == "pending"
        assert outbox.attempts == 1
        outbox.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()

    monkeypatch.undo()
    assert dispatch_outbox(job_id) is True
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        outbox = db.get(DispatchOutbox, job_id)
        assert job.status == "queued"
        assert job.dispatch_error is None
        assert outbox.status == "dispatched"
        assert outbox.dispatched_at is not None


@pytest.mark.parametrize("fail_delivery", [False, True])
def test_expired_dispatch_attempt_cannot_settle_newer_claim(
    monkeypatch, fail_delivery
):
    job_id = _new_job()

    class QueueWithNewerClaim:
        def fetch_job(self, _job_id):
            with SessionLocal() as db:
                outbox = db.get(DispatchOutbox, job_id)
                outbox.attempts += 1
                outbox.lease_expires_at = datetime.now(timezone.utc) + timedelta(
                    seconds=60
                )
                db.commit()
            if fail_delivery:
                raise ConnectionError("redis unavailable")
            return object()

    monkeypatch.setattr("app.dispatcher.queue", QueueWithNewerClaim)
    assert dispatch_outbox(job_id) is False
    with SessionLocal() as db:
        outbox = db.get(DispatchOutbox, job_id)
        assert outbox.status == "dispatching"
        assert outbox.attempts == 2
        assert db.get(Job, job_id).status == "dispatch_pending"


def test_queued_jobs_with_failed_or_stale_missing_deliveries_are_recovered(
    monkeypatch,
):
    failed_id = _new_job("queued")
    missing_id = _new_job("queued")
    recent_missing_id = _new_job("queued")
    waiting_id = _new_job("queued")
    with SessionLocal() as db:
        for job_id in (failed_id, missing_id, recent_missing_id, waiting_id):
            db.get(DispatchOutbox, job_id).status = "dispatched"
        db.get(Job, missing_id).updated_at = datetime.now(timezone.utc) - timedelta(
            seconds=STALE_WORKER_SECONDS + 1
        )
        db.commit()

    class Delivery:
        def __init__(self, status):
            self.status = status

        def get_status(self, refresh=True):
            return self.status

    class QueueWithDeliveries:
        def fetch_job(self, job_id):
            if job_id == failed_id:
                return Delivery("failed")
            if job_id == waiting_id:
                return Delivery("queued")
            return None

    monkeypatch.setattr("app.dispatcher.queue", QueueWithDeliveries)
    assert recover_undelivered_jobs() == 2
    with SessionLocal() as db:
        assert db.get(Job, failed_id).status == "failed"
        assert db.get(Job, missing_id).status == "failed"
        assert db.get(Job, recent_missing_id).status == "queued"
        assert db.get(Job, waiting_id).status == "queued"
        assert db.get(Job, failed_id).error == "worker_delivery_lost"


def test_duplicate_delivery_after_dispatcher_restart_reuses_rq_job():
    job_id = _new_job()
    assert dispatch_outbox(job_id) is True

    with SessionLocal() as db:
        outbox = db.get(DispatchOutbox, job_id)
        outbox.status = "dispatching"
        outbox.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        outbox.dispatched_at = None
        db.commit()

    assert dispatch_outbox(job_id) is True
    from app.core import queue

    assert queue().fetch_job(job_id) is not None
    with SessionLocal() as db:
        assert db.get(DispatchOutbox, job_id).status == "dispatched"


def test_duplicate_worker_delivery_does_not_restart_running_job():
    job_id = _new_job("running")

    from app.worker import execute

    execute(job_id)
    with SessionLocal() as db:
        assert db.get(Job, job_id).status == "running"


def test_stale_worker_recovery_and_retry_create_a_new_job(api_client):
    failed_job_id = _new_job("running")
    with SessionLocal() as db:
        job = db.get(Job, failed_job_id)
        job.updated_at = datetime.now(timezone.utc) - timedelta(
            seconds=STALE_WORKER_SECONDS + 1
        )
        db.commit()

    assert recover_stale_jobs() == 1
    with SessionLocal() as db:
        failed = db.get(Job, failed_job_id)
        assert failed.status == "failed"
        assert failed.error == "worker_lost"

    response = api_client.post(f"/v1/jobs/{failed_job_id}/retry")
    assert response.status_code == 202
    retry_id = response.json()["id"]
    assert retry_id != failed_job_id
    with SessionLocal() as db:
        retry = db.get(Job, retry_id)
        assert retry.retry_of_job_id == failed_job_id
        assert retry.status in {"queued", "running"}


def test_job_idempotency_and_project_media_references(api_client):
    project_id = str(uuid.uuid4())
    media_id = str(uuid.uuid4())
    with SessionLocal() as db:
        db.add(Project(id=project_id, owner="test-producer", name="synthetic"))
        db.flush()
        db.add(
            MediaAsset(
                id=media_id,
                project_id=project_id,
                filename="sample.mp4",
                media_type="video/mp4",
                size_bytes=123,
                sha256="a" * 64,
                storage_key=f"{project_id}/{media_id}",
            )
        )
        db.commit()

    request = {
        "operation": "render_test",
        "project_id": project_id,
        "media_id": media_id,
    }
    headers = {"Idempotency-Key": "render-synthetic-1"}
    first = api_client.post("/v1/jobs", json=request, headers=headers)
    second = api_client.post("/v1/jobs", json=request, headers=headers)
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    with SessionLocal() as db:
        jobs = db.scalars(select(Job)).all()
        assert len(jobs) == 1
        assert jobs[0].project_id == project_id
        assert jobs[0].media_id == media_id
        assert db.get(DispatchOutbox, jobs[0].id) is not None

    conflict = api_client.post(
        "/v1/jobs",
        json={"operation": "render_test", "project_id": project_id},
        headers=headers,
    )
    assert conflict.status_code == 409


def test_cancel_before_dispatch_closes_outbox(api_client):
    job_id = _new_job()
    response = api_client.post(f"/v1/jobs/{job_id}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    with SessionLocal() as db:
        assert db.get(DispatchOutbox, job_id).status == "cancelled"


def test_worker_claim_cannot_overwrite_a_concurrent_cancellation(api_client):
    job_id = _new_job("queued")
    with SessionLocal() as worker_db:
        stale_job = worker_db.get(Job, job_id)
        response = api_client.post(f"/v1/jobs/{job_id}/cancel")
        assert response.status_code == 200

        assert transition(worker_db, stale_job, "running", "worker") is False
        assert stale_job.status == "cancelled"


def test_worker_retries_claim_after_dispatcher_queues_job(monkeypatch, tmp_path):
    job_id = _new_job("dispatch_pending")
    from app import review, worker

    original_transition = worker.transition
    ffmpeg_called = False

    def transition_with_dispatch_race(db, job, status, actor, **kwargs):
        if status == "running" and job.status == "dispatch_pending":
            with SessionLocal() as dispatcher_db:
                dispatcher_job = dispatcher_db.get(Job, job_id)
                original_transition(
                    dispatcher_db, dispatcher_job, "queued", "dispatcher"
                )
        return original_transition(db, job, status, actor, **kwargs)

    def fake_ffmpeg(_job_id, args, timeout):
        assert timeout == 120
        nonlocal ffmpeg_called
        ffmpeg_called = True
        from pathlib import Path

        Path(args[-1]).write_bytes(b"video")

    monkeypatch.setattr(worker, "transition", transition_with_dispatch_race)
    monkeypatch.setattr(worker, "_run_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(worker, "ARTIFACT_ROOT", tmp_path)
    monkeypatch.setattr(review, "ARTIFACT_ROOT", tmp_path)
    worker.execute(job_id)

    assert ffmpeg_called
    with SessionLocal() as db:
        assert db.get(Job, job_id).status == "awaiting_review"


def test_worker_acknowledges_cancellation_winning_failure_transition(
    monkeypatch, api_client, tmp_path
):
    job_id = _new_job("queued")
    from app import worker

    original_transition = worker.transition

    def cancel_before_failure(db, job, status, actor, **kwargs):
        if status == "failed":
            response = api_client.post(f"/v1/jobs/{job_id}/cancel")
            assert response.status_code == 200
        return original_transition(db, job, status, actor, **kwargs)

    def fail_ffmpeg(*_args):
        raise RuntimeError("render failed")

    monkeypatch.setattr(worker, "transition", cancel_before_failure)
    monkeypatch.setattr(worker, "_run_ffmpeg", fail_ffmpeg)
    monkeypatch.setattr(worker, "ARTIFACT_ROOT", tmp_path)
    worker.execute(job_id)

    with SessionLocal() as db:
        assert db.get(Job, job_id).status == "cancelled"
