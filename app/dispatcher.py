from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update

from .core import Audit, DispatchOutbox, Job, SessionLocal, queue, transition

logger = logging.getLogger(__name__)
QUEUE_JOB_TIMEOUT = 3600
DISPATCH_LEASE_SECONDS = 60
STALE_WORKER_SECONDS = QUEUE_JOB_TIMEOUT + 300


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def dispatch_outbox(job_id: str) -> bool:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        outbox = db.scalar(
            select(DispatchOutbox)
            .where(DispatchOutbox.job_id == job_id)
            .with_for_update()
        )
        if outbox is None or outbox.status in {"dispatched", "cancelled"}:
            return False
        if (outbox.status == "dispatching" and outbox.lease_expires_at
                and _utc(outbox.lease_expires_at) > now):
            return False
        if _utc(outbox.available_at) > now:
            return False

        job = db.get(Job, job_id)
        if job is None or job.status in {"cancelled", "failed"}:
            outbox.status = "cancelled"
            outbox.lease_expires_at = None
            db.commit()
            return False

        outbox.status = "dispatching"
        outbox.attempts += 1
        attempts = outbox.attempts
        outbox.lease_expires_at = now + timedelta(seconds=DISPATCH_LEASE_SECONDS)
        db.commit()

    try:
        work_queue = queue()
        existing = work_queue.fetch_job(job_id)
        if existing is None:
            try:
                work_queue.enqueue(
                    "app.worker.execute",
                    job_id,
                    job_id=job_id,
                    job_timeout=QUEUE_JOB_TIMEOUT,
                )
            except ValueError:
                if work_queue.fetch_job(job_id) is None:
                    raise
    except Exception as exc:
        with SessionLocal() as db:
            error_code = type(exc).__name__[:64]
            retry_at = datetime.now(timezone.utc)
            outbox_result = db.execute(
                update(DispatchOutbox)
                .where(
                    DispatchOutbox.job_id == job_id,
                    DispatchOutbox.status == "dispatching",
                    DispatchOutbox.attempts == attempts,
                )
                .values(
                    status="pending",
                    lease_expires_at=None,
                    available_at=retry_at
                    + timedelta(seconds=min(2 ** min(attempts, 8), 300)),
                    last_error=error_code,
                )
            )
            job = db.get(Job, job_id)
            if outbox_result.rowcount == 1 and job is not None:
                status_result = db.execute(
                    update(Job)
                    .where(
                        Job.id == job_id,
                        Job.status.in_(("dispatch_pending", "queued")),
                    )
                    .values(
                        status="dispatch_pending",
                        dispatch_attempts=attempts,
                        dispatch_error=error_code,
                    )
                )
                if status_result.rowcount == 1:
                    db.add(
                        Audit(
                            job_id=job.id,
                            actor="dispatcher",
                            action="dispatch_failed",
                        )
                    )
                db.commit()
        logger.warning("Job dispatch failed: %s (%s)", job_id, type(exc).__name__)
        return False

    with SessionLocal() as db:
        outbox = db.scalar(
            select(DispatchOutbox)
            .where(DispatchOutbox.job_id == job_id)
            .with_for_update()
        )
        job = db.get(Job, job_id)
        if outbox is None:
            return False
        if outbox.status != "dispatching" or outbox.attempts != attempts:
            return False
        outbox.status = "dispatched"
        outbox.lease_expires_at = None
        outbox.dispatched_at = datetime.now(timezone.utc)
        outbox.last_error = None
        if job is not None:
            job.dispatch_attempts = outbox.attempts
            job.dispatch_error = None
            if job.status == "dispatch_pending":
                if not transition(db, job, "queued", "dispatcher"):
                    db.commit()
            else:
                db.add(Audit(job_id=job.id, actor="dispatcher", action="dispatched"))
                db.commit()
        else:
            db.commit()
    return True


def dispatch_due(limit: int = 100) -> int:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        statement = (
            select(DispatchOutbox.job_id)
            .where(
                or_(
                    (DispatchOutbox.status == "pending")
                    & (DispatchOutbox.available_at <= now),
                    (DispatchOutbox.status == "dispatching")
                    & (DispatchOutbox.lease_expires_at <= now),
                )
            )
            .order_by(DispatchOutbox.available_at)
            .limit(limit)
        )
        job_ids = list(db.scalars(statement))
    return sum(dispatch_outbox(job_id) for job_id in job_ids)


def recover_stale_jobs() -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=STALE_WORKER_SECONDS)
    recovered = 0
    with SessionLocal() as db:
        jobs = db.scalars(
            select(Job).where(
                Job.status.in_(("running", "cancel_requested")),
                Job.updated_at < cutoff,
            )
        ).all()
        for job in jobs:
            if job.status == "cancel_requested":
                changed = transition(db, job, "cancelled", "recovery")
            else:
                changed = transition(db, job, "failed", "recovery", error="worker_lost")
            recovered += changed
    return recovered


def recover_undelivered_jobs(limit: int = 100) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=STALE_WORKER_SECONDS)
    with SessionLocal() as db:
        candidates = db.execute(
            select(Job.id)
            .join(DispatchOutbox, DispatchOutbox.job_id == Job.id)
            .where(
                Job.status == "queued",
                DispatchOutbox.status == "dispatched",
            )
            .order_by(Job.updated_at)
            .limit(limit)
        ).scalars().all()

    if not candidates:
        return 0

    work_queue = queue()
    recovered = 0
    for job_id in candidates:
        delivery = work_queue.fetch_job(job_id)
        delivery_status = None
        if delivery is not None:
            delivery_status = delivery.get_status(refresh=True)
            delivery_status = getattr(delivery_status, "value", delivery_status)
        if delivery is not None and delivery_status != "failed":
            continue

        with SessionLocal() as db:
            job = db.get(Job, job_id)
            if (
                job is None
                or job.status != "queued"
                or (delivery is None and job.updated_at >= cutoff)
            ):
                continue
            recovered += transition(
                db,
                job,
                "failed",
                "recovery",
                error="worker_delivery_lost",
            )
    return recovered


def run() -> None:
    while True:
        try:
            dispatch_due()
            recover_undelivered_jobs()
            recover_stale_jobs()
        except Exception as exc:
            logger.error("Dispatcher cycle failed (%s)", type(exc).__name__)
        time.sleep(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
