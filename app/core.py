from datetime import datetime, timezone
from pathlib import Path

from redis import Redis
from rq import Queue
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DDL,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.orm import synonym

from .config import get_settings

settings = get_settings()

DATABASE_URL = settings.database_url
REDIS_URL = settings.redis_url
ARTIFACT_ROOT = Path(settings.artifact_root)
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)

JOB_STATUSES = {
    "dispatch_pending",
    "queued",
    "running",
    "cancel_requested",
    "awaiting_review",
    "approved",
    "rejected",
    "submitted_to_comfyui",
    "failed",
    "cancelled",
}

JOB_TRANSITIONS = {
    "dispatch_pending": {"queued", "running", "cancelled"},
    "queued": {"running", "failed", "cancelled"},
    "running": {
        "cancel_requested",
        "awaiting_review",
        "submitted_to_comfyui",
        "failed",
    },
    "awaiting_review": {"approved", "rejected"},
    "cancel_requested": {"cancelled", "failed"},
}


class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class ProjectMembership(Base):
    __tablename__ = "project_memberships"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_memberships_project_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class MediaArtifact(Base):
    __tablename__ = "media_artifacts"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("projects.id"), nullable=False, index=True
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("projects.id"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("owner", "idempotency_key", name="uq_jobs_owner_idempotency"),
        UniqueConstraint("retry_of_job_id", name="uq_jobs_retry_of_id"),
        CheckConstraint(
            "status IN ('dispatch_pending', 'queued', 'running', 'cancel_requested', "
            "'awaiting_review', 'approved', 'rejected', 'submitted_to_comfyui', "
            "'failed', 'cancelled')",
            name="ck_jobs_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("projects.id"), nullable=True, index=True
    )
    media_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("media_assets.id"), nullable=True, index=True
    )
    retry_of_job_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("jobs.id"), nullable=True, index=True
    )
    retry_of_id = synonym("retry_of_job_id")
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    spec: Mapped[dict] = mapped_column(JSON, nullable=False)
    artifact: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    review_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    __mapper_args__ = {
        "version_id_col": review_version,
        "version_id_generator": lambda previous: 0 if previous is None else previous + 1,
    }
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dispatch_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dispatch_error: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class DispatchOutbox(Base):
    __tablename__ = "dispatch_outbox"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'dispatching', 'dispatched', 'cancelled')",
            name="ck_dispatch_outbox_status",
        ),
    )

    job_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("jobs.id"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )



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
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("jobs.id"), index=True
    )
    actor: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class Review(Base):
    __tablename__ = "job_reviews"
    __table_args__ = (
        UniqueConstraint("job_id", "version", name="uq_job_reviews_version"),
        CheckConstraint("decision IN ('approved', 'rejected')", name="ck_job_reviews_decision"),
        CheckConstraint("expected_version >= 0 AND version = expected_version + 1", name="ck_job_reviews_version"),
        CheckConstraint("contract_version = 1", name="ck_job_reviews_contract"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(40), ForeignKey("jobs.id"), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    artifact_id: Mapped[str] = mapped_column(String(40), nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_version: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    contract_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


def install_review_guards(connection):
    """Append-only evidence, including protection against direct SQL mutation."""
    if connection.dialect.name == "sqlite":
        connection.execute(DDL(
            "CREATE TRIGGER job_reviews_no_replace BEFORE INSERT ON job_reviews "
            "WHEN EXISTS (SELECT 1 FROM job_reviews WHERE id = NEW.id "
            "OR (job_id = NEW.job_id AND version = NEW.version)) "
            "BEGIN SELECT RAISE(ABORT, 'Review evidence is immutable'); END"
        ))
        for action in ("UPDATE", "DELETE"):
            connection.execute(DDL(
                f"CREATE TRIGGER job_reviews_no_{action.lower()} BEFORE {action} ON job_reviews "
                "BEGIN SELECT RAISE(ABORT, 'Review evidence is immutable'); END"
            ))
    elif connection.dialect.name == "postgresql":
        connection.execute(DDL(
            "CREATE OR REPLACE FUNCTION deny_review_mutation() RETURNS trigger LANGUAGE plpgsql AS "
            "$$ BEGIN RAISE EXCEPTION 'Review evidence is immutable'; END; $$"
        ))
        connection.execute(DDL(
            "CREATE TRIGGER job_reviews_immutable BEFORE UPDATE OR DELETE ON job_reviews "
            "FOR EACH ROW EXECUTE FUNCTION deny_review_mutation()"
        ))
        connection.execute(DDL(
            "CREATE TRIGGER job_reviews_no_truncate BEFORE TRUNCATE ON job_reviews "
            "FOR EACH STATEMENT EXECUTE FUNCTION deny_review_mutation()"
        ))
    else:
        raise RuntimeError("Review evidence requires SQLite or PostgreSQL")

@event.listens_for(Review.__table__, "after_create")
def review_guards(target, connection, **kwargs):
    install_review_guards(connection)


def queue(name: str = "video"):
    return Queue(
        name,
        connection=Redis.from_url(
            REDIS_URL, socket_connect_timeout=5, socket_timeout=5
        ),
        default_timeout=3600,
    )


def get_db():
    with SessionLocal() as db:
        yield db


def transition(
    db,
    job,
    status,
    actor,
    error=None,
    artifact=None,
    artifact_id=None,
    artifact_sha256=None,
):
    db.flush()
    current_status = job.status
    if current_status == status:
        return True
    if status not in JOB_TRANSITIONS.get(current_status, set()):
        raise ValueError(f"Invalid job transition: {current_status} -> {status}")
    updated_at = datetime.now(timezone.utc)
    values = {
        "status": status,
        "updated_at": updated_at,
        "review_version": job.review_version + 1,
    }
    if error is not None:
        values["error"] = error[:2000]
    if artifact is not None:
        values["artifact"] = artifact
        if artifact_id is not None:
            values["artifact_id"] = artifact_id
        if artifact_sha256 is not None:
            values["artifact_sha256"] = artifact_sha256
    try:
        with db.no_autoflush:
            result = db.execute(
                update(Job)
                .where(
                    Job.id == job.id,
                    Job.owner == job.owner,
                    Job.status == current_status,
                    Job.review_version == job.review_version,
                )
                .values(**values)
                .execution_options(synchronize_session=False)
            )
        if result.rowcount != 1:
            db.rollback()
            db.refresh(job)
            return False
        db.add(Audit(job_id=job.id, actor=actor, action=status))
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(job)
    return True
