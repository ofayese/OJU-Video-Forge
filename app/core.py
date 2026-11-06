from datetime import datetime, timezone
from pathlib import Path

from redis import Redis
from rq import Queue
from sqlalchemy import CheckConstraint, DDL, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, create_engine, event, update
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
    owner: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default='queued')
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
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class Audit(Base):
    __tablename__ = 'audit_events'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(40), ForeignKey('jobs.id'), index=True)
    actor: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

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
            "CREATE FUNCTION deny_review_mutation() RETURNS trigger LANGUAGE plpgsql AS "
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

def queue():
    return Queue('video', connection=Redis.from_url(REDIS_URL), default_timeout=3600)

def transition(db, job, status, actor, error=None, artifact=None, artifact_id=None, artifact_sha256=None):
    """Fenced worker/dispatch transition; never flush stale ORM job state.

    The stored monotonic version is shared by all job writers, not audit-derived.
    Review decisions use their own single transaction with both evidence tables.
    """
    allowed = {
        "queued": {"running", "dispatch_pending"},
        "dispatch_pending": {"running"},
        "running": {"awaiting_review", "submitted_to_comfyui", "failed"},
    }
    if status not in allowed.get(job.status, set()):
        return False
    values = {"status": status, "review_version": job.review_version + 1}
    if error is not None:
        values["error"] = error[:2000]
    if artifact is not None:
        values.update(artifact=artifact, artifact_id=artifact_id, artifact_sha256=artifact_sha256)
    try:
        with db.no_autoflush:
            result = db.execute(
                update(Job).where(
                    Job.id == job.id, Job.owner == job.owner,
                    Job.status == job.status, Job.review_version == job.review_version,
                ).values(**values),
                execution_options={"synchronize_session": False},
            )
        if result.rowcount != 1:
            db.rollback()
            return False
        db.add(Audit(job_id=job.id, actor=actor, action=status))
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(job)
    return True
