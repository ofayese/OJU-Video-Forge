import uuid
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from sqlalchemy import select

from .auth import Actor, require_roles
from .core import Audit, Job, Review, SessionLocal, queue, transition
from .config import get_settings
from .review import ArtifactIdentity, ReviewDecision, decide, eligible

app = FastAPI(title="OJU Video Forge", version="0.1.0", docs_url=None, redoc_url=None, openapi_url="/openapi.json")


class CreateJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: str
    prompt: str = Field(default="", max_length=1000)
    width: int = Field(default=512, ge=256, le=1920)
    height: int = Field(default=288, ge=144, le=1080)
    frames: int = Field(default=33, ge=17, le=81)
    fps: int = Field(default=16, ge=8, le=24)
    seed: int = Field(default=42, ge=0, le=2**32 - 1)


class JobResult(BaseModel):
    id: str
    operation: str
    status: str
    artifact: str | None
    error: str | None
    review_contract_version: int = 1
    review_version: int
    review_enabled: bool
    can_review: bool
    review_artifact: ArtifactIdentity | None


class ReviewResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    contract_version: int
    job_id: str
    actor: str
    at: datetime
    artifact_id: str
    artifact_sha256: str
    expected_version: int
    version: int
    decision: str


READ_ROLES = ("viewer", "producer", "reviewer", "publisher", "auditor", "administrator", "agent_service")


def owned_job(db, job_id, actor):
    job = db.get(Job, job_id)
    if not job or job.owner != actor.name:
        raise HTTPException(404, "Job not found")
    return job


def job_result(job, actor):
    enabled = get_settings().review_workflow_enabled
    return JobResult(
        id=job.id, operation=job.operation, status=job.status,
        artifact=job.artifact, error=job.error,
        review_version=job.review_version, review_enabled=enabled,
        can_review=enabled and eligible(job) and bool({"reviewer", "administrator"} & set(actor.roles)),
        review_artifact=ArtifactIdentity(id=job.artifact_id, sha256=job.artifact_sha256)
        if job.artifact_id and job.artifact_sha256 else None,
    )


def db_session():
    with SessionLocal() as db:
        yield db


@app.get("/health/live", include_in_schema=False)
def live():
    return {"ok": True}


@app.post("/v1/jobs", response_model=JobResult, status_code=202, operation_id="submitVideoJob")
def create_job(
    payload: CreateJob,
    actor: Actor = Depends(require_roles("producer", "administrator", "agent_service")),
    db: Session = Depends(db_session),
):
    if payload.operation == "wan22_t2v" and not payload.prompt.strip():
        raise HTTPException(422, "Prompt required for AI generation")
    job = Job(
        id=str(uuid.uuid4()),
        owner=actor.name,
        operation=payload.operation,
        status="queued",
        spec=payload.model_dump(),
    )
    db.add(job)
    db.add(Audit(job_id=job.id, actor=actor.name, action="queued"))
    db.commit()
    try:
        queue().enqueue("app.worker.execute", job.id, job_id=job.id, job_timeout=3600)
    except Exception:
        transition(db, job, "dispatch_pending", actor.name)
    return job_result(job, actor)


@app.get("/v1/jobs/{job_id}", response_model=JobResult, operation_id="getVideoJob")
def get_job(
    job_id: str,
    actor: Actor = Depends(require_roles(*READ_ROLES)),
    db: Session = Depends(db_session),
):
    return job_result(owned_job(db, job_id, actor), actor)


@app.get("/v1/jobs/{job_id}/reviews", response_model=list[ReviewResult], operation_id="listJobReviews")
def get_reviews(
    job_id: str,
    after_version: int = Query(default=0, ge=0, le=2**31 - 1),
    limit: int = Query(default=50, ge=1, le=100),
    actor: Actor = Depends(require_roles(*READ_ROLES)),
    db: Session = Depends(db_session),
):
    owned_job(db, job_id, actor)
    return db.scalars(select(Review).where(
        Review.job_id == job_id, Review.version > after_version,
    ).order_by(Review.version).limit(limit)).all()


@app.post("/v1/jobs/{job_id}/review", response_model=JobResult, operation_id="reviewJobArtifact")
def review_job(
    job_id: str,
    payload: ReviewDecision,
    actor: Actor = Depends(require_roles(*READ_ROLES)),
    db: Session = Depends(db_session),
):
    job = owned_job(db, job_id, actor)
    if not {"reviewer", "administrator"} & set(actor.roles):
        raise HTTPException(403, "Insufficient permissions")
    if not get_settings().review_workflow_enabled:
        raise HTTPException(503, "Review workflow disabled; history is preserved")
    return job_result(decide(db, job, actor, payload), actor)


@app.get("/v1/models", operation_id="listVideoModels")
def get_models(
    actor: Actor = Depends(require_roles("viewer", "producer", "reviewer", "publisher", "auditor", "administrator", "agent_service"))
):
    return {"models": [{"id": "wan22-ti2v-5b", "operation": "wan22_t2v", "status": "requires_workflow_registration"}]}
