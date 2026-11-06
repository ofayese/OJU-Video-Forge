from datetime import datetime, timezone
from pathlib import Path

from redis import Redis
from rq import Queue
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, create_engine
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

class Job(Base):
    __tablename__ = 'jobs'
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey('projects.id'), nullable=True, index=True)
    owner: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default='queued')
    spec: Mapped[dict] = mapped_column(JSON, nullable=False)
    artifact: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class Project(Base):
    __tablename__ = 'projects'
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    archived: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class ProjectMembership(Base):
    __tablename__ = 'project_memberships'
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True)
    actor: Mapped[str] = mapped_column(String(128), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)

class AgentProposal(Base):
    __tablename__ = 'agent_proposals'
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id'), nullable=False, index=True)
    proposer: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    arguments: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='pending')
    approver: Mapped[str | None] = mapped_column(String(128), nullable=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey('jobs.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class AgentAudit(Base):
    __tablename__ = 'agent_audit_events'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id'), nullable=False, index=True)
    proposal_id: Mapped[str] = mapped_column(ForeignKey('agent_proposals.id'), nullable=False, index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey('jobs.id'), nullable=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

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
