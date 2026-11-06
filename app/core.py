from datetime import datetime, timezone
from pathlib import Path

from redis import Redis
from rq import Queue
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    create_engine,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import get_settings

settings = get_settings()

DATABASE_URL = settings.database_url
REDIS_URL = settings.redis_url
ARTIFACT_ROOT = Path(settings.artifact_root)
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_timeout=2,
    connect_args={"connect_timeout": 2},
)
SessionLocal = sessionmaker(engine, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

class Job(Base):
    __tablename__ = 'jobs'
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default='queued')
    spec: Mapped[dict] = mapped_column(JSON, nullable=False)
    artifact: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    recovery_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class Audit(Base):
    __tablename__ = 'audit_events'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(40), ForeignKey('jobs.id'), index=True)
    actor: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

def queue():
    return Queue('video', connection=Redis.from_url(REDIS_URL), default_timeout=3600)

def transition(db, job, status, actor, error=None, artifact=None):
    job.status = status
    if error is not None:
        job.error = error[:2000]
    if artifact is not None:
        job.artifact = artifact
    db.add(Audit(job_id=job.id, actor=actor, action=status))
    db.commit()


MAX_INTERRUPTED_RECOVERIES = 1


def _queue_job_id(job):
    if job.recovery_count:
        return f"{job.id}:recovery:{job.recovery_count}"
    return job.id


def recover_jobs(db, job_queue):
    recovered = 0
    jobs = db.scalars(
        select(Job)
        .where(Job.status.in_(("queued", "dispatch_pending", "running")))
        .with_for_update()
    ).all()
    for job in jobs:
        queued_job = job_queue.fetch_job(_queue_job_id(job))
        queue_status = (
            queued_job.get_status(refresh=True) if queued_job is not None else None
        )
        queue_status = getattr(queue_status, "value", queue_status)
        if queue_status in {"queued", "started"} and job.status != "running":
            continue

        interrupted = job.status == "running" or queued_job is not None
        if interrupted and job.operation != "render_test":
            transition(
                db,
                job,
                "failed",
                "recovery",
                error="Interrupted operation requires explicit resubmission",
            )
            continue
        if interrupted and job.recovery_count >= MAX_INTERRUPTED_RECOVERIES:
            transition(
                db,
                job,
                "failed",
                "recovery",
                error="Interrupted job recovery limit exceeded",
            )
            continue
        if interrupted:
            job.recovery_count += 1
            job.status = "dispatch_pending"
            job.error = None
            db.add(Audit(job_id=job.id, actor="recovery", action="interrupted_recovery"))
            db.commit()

        job_queue.enqueue(
            "app.worker.execute",
            job.id,
            job_id=_queue_job_id(job),
            job_timeout=3600,
        )
        recovered += 1
    return recovered
