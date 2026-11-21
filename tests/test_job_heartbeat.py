"""Liveness-signal regression tests for long-running jobs.

Background: ``recover_stale_jobs()`` fails a ``running`` job as ``worker_lost``
purely from the age of ``Job.updated_at``. Only the timeline render harness used to
refresh that column, so a healthy but slow GPU (``wan22_t2v``) or narration
(``narrate_existing_video``) job could be failed while it was still working.

These tests exercise the in-band heartbeat in ``app.worker._JobHeartbeat``:

* it refreshes ``updated_at`` for the current execution,
* it is rate-limited (no unbounded write load),
* it is ownership-fenced: once the job leaves ``running`` (failed by recovery or
  cancelled) a late heartbeat cannot revive or re-own it,
* a heartbeat database failure is visible but never fails a healthy render.

They require the same disposable services as the other dispatch suites and skip by
design when those are absent.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app import worker
from app.core import Base, Job, SessionLocal, engine
from app.dispatcher import (
    GPU_STALE_WORKER_SECONDS,
    STALE_WORKER_SECONDS,
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
        "job heartbeat tests require APP_ENV=test and disposable "
        "*_test PostgreSQL plus Redis database 15"
    ),
)


@pytest.fixture(autouse=True)
def isolated_database():
    assert _isolated_service_urls()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


def _new_job(status: str = "running", operation: str = "wan22_t2v") -> str:
    job_id = str(uuid.uuid4())
    with SessionLocal() as db:
        db.add(
            Job(
                id=job_id,
                owner="test-producer",
                operation=operation,
                status=status,
                spec={"operation": operation},
                updated_at=datetime.now(UTC) - timedelta(seconds=10),
            )
        )
        db.commit()
    return job_id


def _updated_at(job_id: str) -> datetime:
    with SessionLocal() as db:
        value = db.scalar(select(Job.updated_at).where(Job.id == job_id))
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def test_heartbeat_refreshes_running_job():
    """A healthy long-running job's liveness signal advances updated_at."""
    job_id = _new_job()
    before = _updated_at(job_id)

    heartbeat = worker._JobHeartbeat(job_id)
    heartbeat.tick()

    assert _updated_at(job_id) > before


def test_heartbeat_is_rate_limited():
    """Repeated ticks inside the interval must not issue extra writes."""
    job_id = _new_job()
    heartbeat = worker._JobHeartbeat(job_id, interval=3600.0)
    heartbeat.tick()
    first = _updated_at(job_id)

    for _ in range(20):
        heartbeat.tick()

    assert _updated_at(job_id) == first


def test_heartbeat_keeps_gpu_job_out_of_stale_recovery():
    """A heartbeating GPU job is never selected by stale recovery.

    This is the defect under test: the previous code sent no heartbeat from the
    GPU path, so a run older than GPU_STALE_WORKER_SECONDS was failed as
    worker_lost even though it was still processing.
    """
    job_id = _new_job()
    # Age the job past the GPU stale threshold with no heartbeat applied...
    with SessionLocal() as db:
        db.get(Job, job_id).updated_at = datetime.now(UTC) - timedelta(
            seconds=GPU_STALE_WORKER_SECONDS + 60
        )
        db.commit()

    # ...then tick: the executor is demonstrably alive.
    worker._JobHeartbeat(job_id).tick()

    assert recover_stale_jobs() == 0
    with SessionLocal() as db:
        assert db.get(Job, job_id).status == "running"
        assert db.get(Job, job_id).error is None


def test_heartbeat_keeps_narration_job_out_of_stale_recovery():
    """The non-GPU (narration) cutoff is also respected by the heartbeat."""
    job_id = _new_job(operation="narrate_existing_video")
    with SessionLocal() as db:
        db.get(Job, job_id).updated_at = datetime.now(UTC) - timedelta(
            seconds=STALE_WORKER_SECONDS + 60
        )
        db.commit()

    worker._JobHeartbeat(job_id).tick()

    assert recover_stale_jobs() == 0
    with SessionLocal() as db:
        assert db.get(Job, job_id).status == "running"


def test_still_detects_genuinely_dead_job():
    """Crash detection must not be weakened: an unrefreshed job still fails."""
    job_id = _new_job()
    with SessionLocal() as db:
        db.get(Job, job_id).updated_at = datetime.now(UTC) - timedelta(
            seconds=GPU_STALE_WORKER_SECONDS + 60
        )
        db.commit()

    # No heartbeat tick: the worker is presumed dead.
    assert recover_stale_jobs() == 1
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        assert job.status == "failed"
        assert job.error == "worker_lost"


def test_late_heartbeat_cannot_revive_recovered_job():
    """Ownership fencing: a heartbeat after recovery must not re-own the job.

    The UPDATE is conditioned on status == 'running'. Once recovery has written
    'failed', a delayed heartbeat from the old worker matches no row.
    """
    job_id = _new_job()
    with SessionLocal() as db:
        db.get(Job, job_id).updated_at = datetime.now(UTC) - timedelta(
            seconds=GPU_STALE_WORKER_SECONDS + 60
        )
        db.commit()

    assert recover_stale_jobs() == 1
    failed_at = _updated_at(job_id)

    # The original worker finally ticks.
    worker._JobHeartbeat(job_id).tick()

    with SessionLocal() as db:
        job = db.get(Job, job_id)
        assert job.status == "failed", "heartbeat revived a recovered job"
    assert _updated_at(job_id) == failed_at, "heartbeat wrote after ownership was lost"


def test_late_heartbeat_cannot_revive_cancelled_job():
    """Cancellation wins over a late heartbeat from the active worker.

    ``recover_stale_jobs()`` returns the number of terminal transitions it applied.
    A ``cancel_requested`` job is finalized as ``cancelled`` by the worker itself
    (``execute()`` / ``_finish_or_cancel()``), by the next dispatcher cycle, or by
    this recovery sweep. The sweep therefore legitimately reports **0** when none of
    its *other* candidates needed changing - 0 is an attempt/transition count, not a
    success flag, and the production dispatcher loop discards it (``app/dispatcher.py``
    ``run()``).

    To make the assertion deterministic the sweep is given exactly one stale
    ``running`` job alongside the cancelled target. ``recover_stale_jobs()`` counts a
    ``cancel_requested`` job that it finalizes (``cancel_requested -> cancelled`` is
    itself a terminal transition), so the sweep reports 2; a job already marked
    ``cancelled`` would contribute 0 because ``transition()`` is a no-op for a status
    that is already current.
    """
    cancelled_id = _new_job(status="cancel_requested")
    stale_id = _new_job()  # unrelated stale attempt
    for job_id in (cancelled_id, stale_id):
        with SessionLocal() as db:
            db.get(Job, job_id).updated_at = datetime.now(UTC) - timedelta(
                seconds=GPU_STALE_WORKER_SECONDS + 60
            )
            db.commit()

    assert recover_stale_jobs() == 2
    with SessionLocal() as db:
        assert db.get(Job, stale_id).status == "failed"
        assert db.get(Job, stale_id).error == "worker_lost"
        assert db.get(Job, cancelled_id).status == "cancelled"

    cancelled_at = _updated_at(cancelled_id)
    worker._JobHeartbeat(cancelled_id).tick()
    with SessionLocal() as db:
        assert db.get(Job, cancelled_id).status == "cancelled"
    assert _updated_at(cancelled_id) == cancelled_at, "heartbeat wrote after cancellation"


def test_heartbeat_failure_is_visible_but_non_fatal(monkeypatch, caplog):
    """A database error during heartbeat logs a warning and does not raise."""
    job_id = _new_job()

    class BrokenSession:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def scalar(self, *_a, **_k):
            # The constructor snapshots the attempt token; fail that read too so
            # this exercises the real error path rather than an AttributeError.
            raise RuntimeError("database unavailable")

        def execute(self, *_a, **_k):
            raise RuntimeError("database unavailable")

        def commit(self):
            raise AssertionError("commit must not run after a failed execute")

    monkeypatch.setattr(worker, "SessionLocal", BrokenSession)

    with caplog.at_level("WARNING"):
        heartbeat = worker._JobHeartbeat(job_id, interval=0.0)
        assert heartbeat._attempt_version is None, "snapshot failure must disable the heartbeat"
        heartbeat.tick()  # must not raise

    # ``record.message`` is only populated after a formatter has run; asserting on it
    # directly is order-dependent. ``getMessage()`` applies the %-arguments whether or
    # not caplog.formatter ran. The warning names the job and the exception *type* only
    # - never the SQL statement, the database URL or any credential.
    assert any(
        "heartbeat disabled" in rec.getMessage()
        and job_id in rec.getMessage()
        and "RuntimeError" in rec.getMessage()
        for rec in caplog.records
    )


def test_heartbeat_does_not_fail_healthy_render_on_transient_error(monkeypatch):
    """A heartbeat hiccup leaves the job running for a later successful tick."""
    job_id = _new_job()
    real_session = worker.SessionLocal
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            class Boom:
                def __enter__(self):
                    raise RuntimeError("transient")
                def __exit__(self, *exc):
                    return False
            return Boom()
        return real_session()

    monkeypatch.setattr(worker, "SessionLocal", flaky)
    heartbeat = worker._JobHeartbeat(job_id, interval=0.0)
    heartbeat.tick()  # first tick fails, non-fatally
    heartbeat.tick()  # retried on the next tick

    with SessionLocal() as db:
        assert db.get(Job, job_id).status == "running"


def test_previous_execution_cannot_refresh_a_newer_attempt():
    """Cross-execution fence: an old attempt must not keep a new attempt alive.

    Status-only fencing is not sufficient. ``recover_jobs()`` (engine startup)
    resets the SAME job row to ``dispatch_pending`` and re-enqueues it, so the row
    can legitimately return to ``running`` while the original attempt's process is
    still alive (for example a worker that is slow, partitioned, or was displaced).

    A stale tick from that old attempt must therefore NOT refresh ``updated_at``
    while a newer attempt owns the row: doing so would let a dead attempt keep
    ``recover_stale_jobs()`` from ever detecting the new attempt's real death.

    ``_JobHeartbeat`` snapshots the job's ``review_version`` at construction (the
    optimistic-concurrency token advanced by every ``transition()`` and ``Job``
    UPDATE), which distinguishes consecutive executions of one job row.
    """
    job_id = _new_job()
    with SessionLocal() as db:
        old_attempt_version = db.get(Job, job_id).review_version
    old_heartbeat = worker._JobHeartbeat(job_id)

    # The row goes round the loop and a NEWER execution starts on the same row.
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        job.status = "dispatch_pending"
        db.commit()
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        job.status = "running"
        db.commit()
    with SessionLocal() as db:
        newer_version = db.get(Job, job_id).review_version
        db.get(Job, job_id).updated_at = datetime.now(UTC) - timedelta(seconds=600)
        db.commit()
    assert newer_version > old_attempt_version, "fixture did not advance the attempt token"
    newer_updated_at = _updated_at(job_id)

    # The displaced original attempt finally ticks.
    old_heartbeat.tick()

    assert _updated_at(job_id) == newer_updated_at, (
        "heartbeat from a previous execution refreshed a newer attempt"
    )


def test_current_execution_still_heartbeats_after_reattach():
    """Positive control: a heartbeat built for the current execution does tick."""
    job_id = _new_job()
    with SessionLocal() as db:
        db.get(Job, job_id).updated_at = datetime.now(UTC) - timedelta(seconds=600)
        db.commit()
    before = _updated_at(job_id)

    worker._JobHeartbeat(job_id).tick()

    assert _updated_at(job_id) > before
