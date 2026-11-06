from datetime import datetime, timezone
from pathlib import Path

from redis import Redis
from rq import Queue
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import get_settings

settings = get_settings()

DATABASE_URL = settings.database_url
REDIS_URL = settings.redis_url
ARTIFACT_ROOT = Path(settings.artifact_root)
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)

class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = 'projects'
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Job(Base):
    __tablename__ = 'jobs'
    __table_args__ = (UniqueConstraint('retry_of_id', name='uq_jobs_retry_of_id'),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey('projects.id'), nullable=True, index=True)
    retry_of_id: Mapped[str | None] = mapped_column(ForeignKey('jobs.id'), nullable=True)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default='queued')
    spec: Mapped[dict] = mapped_column(JSON, nullable=False)
    artifact: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class AudioAsset(Base):
    __tablename__ = 'audio_assets'
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id'), nullable=False, index=True)
    owner: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    relative_path: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    media_type: Mapped[str] = mapped_column(String(64), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class WorkflowArtifact(Base):
    __tablename__ = 'workflow_artifacts'
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id'), nullable=False, index=True)
    owner: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    relative_path: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    filename: Mapped[str] = mapped_column(String(128), nullable=False)
    media_type: Mapped[str] = mapped_column(String(64), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


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
