import uuid

from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import Actor, require_roles
from .core import Audit, DispatchOutbox, Job, MediaAsset, Project, SessionLocal, transition
from .dispatcher import dispatch_outbox

app = FastAPI(title="OJU Video Forge", version="0.1.0", docs_url=None, redoc_url=None, openapi_url="/openapi.json")


class CreateJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: str
    project_id: str | None = None
    media_id: str | None = None
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
    dispatch_attempts: int = 0
    dispatch_error: str | None = None


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
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if payload.operation == "wan22_t2v" and not payload.prompt.strip():
        raise HTTPException(422, "Prompt required for AI generation")
    if idempotency_key is not None and not 1 <= len(idempotency_key) <= 128:
        raise HTTPException(422, "Invalid Idempotency-Key")
    if payload.media_id and not payload.project_id:
        raise HTTPException(422, "Project required for media jobs")
    project = None
    if payload.project_id:
        project = db.get(Project, payload.project_id)
        if project is None or project.owner != actor.name:
            raise HTTPException(404, "Project not found")
    if payload.media_id:
        media = db.get(MediaAsset, payload.media_id)
        if media is None or media.project_id != payload.project_id:
            raise HTTPException(404, "Media not found")
    spec = payload.model_dump(exclude={"project_id", "media_id"})
    if idempotency_key:
        existing = db.scalar(
            select(Job).where(
                Job.owner == actor.name,
                Job.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if not _same_job_request(existing, payload, spec):
                raise HTTPException(409, "Idempotency-Key already used for a different request")
            return _job_result(existing)
    job = Job(
        id=str(uuid.uuid4()),
        owner=actor.name,
        operation=payload.operation,
        project_id=payload.project_id,
        media_id=payload.media_id,
        status="dispatch_pending",
        spec=spec,
        idempotency_key=idempotency_key,
    )
    try:
        db.add(job)
        db.flush()
        db.add(DispatchOutbox(job_id=job.id, status="pending"))
        db.add(Audit(job_id=job.id, actor=actor.name, action="dispatch_pending"))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        existing = None
        if idempotency_key:
            existing = db.scalar(
                select(Job).where(
                    Job.owner == actor.name,
                    Job.idempotency_key == idempotency_key,
                )
            )
        if existing is not None:
            if _same_job_request(existing, payload, spec):
                return _job_result(existing)
            raise HTTPException(
                409, "Idempotency-Key already used for a different request"
            ) from exc
        raise HTTPException(409, "Job could not be created") from exc
    dispatch_outbox(job.id)
    db.refresh(job)
    return _job_result(job)


@app.get("/v1/jobs/{job_id}", response_model=JobResult, operation_id="getVideoJob")
def get_job(
    job_id: str,
    actor: Actor = Depends(require_roles("viewer", "producer", "reviewer", "publisher", "auditor", "administrator", "agent_service")),
    db: Session = Depends(db_session),
):
    job = db.get(Job, job_id)
    if not job or job.owner != actor.name:
        raise HTTPException(404, "Job not found")
    return _job_result(job)


@app.post(
    "/v1/jobs/{job_id}/retry",
    response_model=JobResult,
    status_code=202,
    operation_id="retryVideoJobDispatch",
)
def retry_job(
    job_id: str,
    actor: Actor = Depends(require_roles("producer", "administrator", "agent_service")),
    db: Session = Depends(db_session),
):
    job = db.get(Job, job_id)
    if not job or job.owner != actor.name:
        raise HTTPException(404, "Job not found")
    if job.status == "failed":
        retry = Job(
            id=str(uuid.uuid4()),
            owner=job.owner,
            project_id=job.project_id,
            media_id=job.media_id,
            retry_of_job_id=job.id,
            operation=job.operation,
            status="dispatch_pending",
            spec=job.spec,
        )
        db.add(retry)
        db.flush()
        db.add(DispatchOutbox(job_id=retry.id, status="pending"))
        db.add(Audit(job_id=job.id, actor=actor.name, action="retry_requested"))
        db.add(Audit(job_id=retry.id, actor=actor.name, action="retry_created"))
        db.commit()
        dispatch_outbox(retry.id)
        db.refresh(retry)
        return _job_result(retry)
    if job.status != "dispatch_pending":
        raise HTTPException(409, "Only dispatch-pending or failed jobs can be retried")
    outbox = db.get(DispatchOutbox, job_id)
    if outbox is None:
        raise HTTPException(409, "Dispatch intent is unavailable")
    outbox.status = "pending"
    outbox.available_at = datetime.now(timezone.utc)
    outbox.lease_expires_at = None
    db.add(Audit(job_id=job.id, actor=actor.name, action="retry_requested"))
    db.commit()
    dispatch_outbox(job.id)
    db.refresh(job)
    return _job_result(job)


@app.post(
    "/v1/jobs/{job_id}/cancel",
    response_model=JobResult,
    operation_id="cancelVideoJob",
)
def cancel_job(
    job_id: str,
    actor: Actor = Depends(require_roles("producer", "administrator", "agent_service")),
    db: Session = Depends(db_session),
):
    job = db.get(Job, job_id)
    if not job or job.owner != actor.name:
        raise HTTPException(404, "Job not found")
    for _ in range(3):
        db.refresh(job)
        if job.status in {
            "awaiting_review",
            "submitted_to_comfyui",
            "failed",
            "cancelled",
        }:
            raise HTTPException(409, "Job can no longer be cancelled")
        if job.status == "cancel_requested":
            return _job_result(job)
        if job.status == "running":
            target_status = "cancel_requested"
        elif job.status in {"dispatch_pending", "queued"}:
            target_status = "cancelled"
        else:
            raise HTTPException(409, "Job can no longer be cancelled")
        if transition(db, job, target_status, actor.name):
            break
    else:
        raise HTTPException(409, "Job changed while cancellation was requested")
    if job.status == "cancelled":
        db.execute(
            update(DispatchOutbox)
            .where(
                DispatchOutbox.job_id == job_id,
                DispatchOutbox.status.in_(("pending", "dispatching")),
            )
            .values(status="cancelled", lease_expires_at=None)
        )
        db.commit()
    return _job_result(job)


def _job_result(job: Job) -> JobResult:
    return JobResult(
        id=job.id,
        operation=job.operation,
        status=job.status,
        artifact=job.artifact,
        error=job.error,
        dispatch_attempts=job.dispatch_attempts,
        dispatch_error=job.dispatch_error,
    )


def _same_job_request(job: Job, payload: CreateJob, spec: dict) -> bool:
    return (
        job.project_id == payload.project_id
        and job.media_id == payload.media_id
        and job.spec == spec
    )


@app.get("/v1/models", operation_id="listVideoModels")
def get_models(
    actor: Actor = Depends(require_roles("viewer", "producer", "reviewer", "publisher", "auditor", "administrator", "agent_service"))
):
    return {"models": [{"id": "wan22-ti2v-5b", "operation": "wan22_t2v", "status": "requires_workflow_registration"}]}
