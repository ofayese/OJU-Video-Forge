import uuid
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import Actor, require_roles
from .config import get_settings
from .core import ARTIFACT_ROOT, Audit, Job, SessionLocal, queue

app = FastAPI(title="OJU Video Forge", version="0.1.0", docs_url=None, redoc_url=None, openapi_url="/openapi.json")


class CreateJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["render_test", "wan22_t2v"]
    prompt: str = Field(default="", max_length=1000)
    width: int = Field(default=512, strict=True, ge=256, le=1280)
    height: int = Field(default=288, strict=True, ge=144, le=720)
    frames: int = Field(default=33, strict=True, ge=17, le=81)
    fps: int = Field(default=16, strict=True, ge=8, le=24)
    seed: int = Field(default=42, strict=True, ge=0, le=2**32 - 1)

    @model_validator(mode="after")
    def validate_generation_controls(self):
        if self.operation == "wan22_t2v":
            if not self.prompt.strip():
                raise ValueError("Prompt required for AI generation")
            if any(not char.isprintable() and char not in "\n\t" for char in self.prompt):
                raise ValueError("Prompt contains unsupported control characters")
            if self.width % 16 or self.height % 16 or (self.frames - 1) % 4:
                raise ValueError("Generation dimensions and frame count must match the Wan workflow")
            if self.fps not in {8, 16, 24}:
                raise ValueError("Unsupported generation frame rate")
        return self


class JobResult(BaseModel):
    id: str
    operation: str
    status: str
    artifact: str | None = Field(description="Authenticated artifact download URL when ready")
    error: str | None


def db_session():
    with SessionLocal() as db:
        yield db


def _enqueue(job: Job, db: Session) -> None:
    queue_name = "gpu" if job.operation == "wan22_t2v" else "video"
    try:
        queue(queue_name).enqueue(
            "app.worker.execute", job.id, job_id=job.id,
            job_timeout=7200 if queue_name == "gpu" else 3600,
        )
    except Exception:
        job.status = "dispatch_pending"
        db.commit()


def _artifact_url(job: Job) -> str | None:
    return f"/v1/jobs/{job.id}/artifact" if job.status == "awaiting_review" and job.artifact else None


@app.get("/health/live", include_in_schema=False)
def live():
    return {"ok": True}


@app.post("/v1/jobs", response_model=JobResult, status_code=202, operation_id="submitVideoJob")
def create_job(
    payload: CreateJob,
    actor: Actor = Depends(require_roles("producer", "administrator", "agent_service")),
    db: Session = Depends(db_session),
):
    if payload.operation == "wan22_t2v" and not get_settings().comfyui_enabled:
        raise HTTPException(503, "ComfyUI generation is disabled")
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
    _enqueue(job, db)
    return JobResult(id=job.id, operation=job.operation, status=job.status, artifact=None, error=None)


@app.get("/v1/jobs/{job_id}", response_model=JobResult, operation_id="getVideoJob")
def get_job(
    job_id: str,
    actor: Actor = Depends(require_roles("viewer", "producer", "reviewer", "publisher", "auditor", "administrator", "agent_service")),
    db: Session = Depends(db_session),
):
    job = db.get(Job, job_id)
    if not job or job.owner != actor.name:
        raise HTTPException(404, "Job not found")
    return JobResult(id=job.id, operation=job.operation, status=job.status, artifact=_artifact_url(job), error=job.error)


@app.post("/v1/jobs/{job_id}/retry", response_model=JobResult, status_code=202, operation_id="retryVideoJob")
def retry_job(
    job_id: str,
    actor: Actor = Depends(require_roles("producer", "administrator", "agent_service")),
    db: Session = Depends(db_session),
):
    original = db.execute(
        select(Job).where(Job.id == job_id).with_for_update()
    ).scalar_one_or_none()
    if not original or original.owner != actor.name:
        raise HTTPException(404, "Job not found")
    if original.status != "failed":
        raise HTTPException(409, "Only failed jobs can be retried")
    if original.operation == "wan22_t2v" and not get_settings().comfyui_enabled:
        raise HTTPException(503, "ComfyUI generation is disabled")

    root_id = original.spec.get("_retry_root", original.id)
    retry_count = db.query(Audit).filter_by(
        job_id=root_id, action="retry_requested"
    )
    if not isinstance(root_id, str):
        raise HTTPException(409, "Retry history is invalid")
    root = db.execute(
        select(Job).where(Job.id == root_id).with_for_update()
    ).scalar_one_or_none()
    if not root or root.owner != actor.name:
        raise HTTPException(409, "Retry history is invalid")
    retry_count = retry_count.count()
    if retry_count >= 3:
        raise HTTPException(409, "Retry limit reached")

    if not isinstance(root_id, str):
        raise HTTPException(409, "Retry history is invalid")
    job = Job(
        id=str(uuid.uuid4()),
        owner=original.owner,
        operation=original.operation,
        status="queued",
        spec={**original.spec, "_retry_root": root_id, "_retry_of": original.id},
    )
    db.add(Audit(job_id=root_id, actor=actor.name, action="retry_requested"))
    db.add(job)
    db.add(Audit(job_id=job.id, actor=actor.name, action=f"retry_of:{original.id}"))
    db.add(Audit(job_id=job.id, actor=actor.name, action="queued"))
    db.commit()
    _enqueue(job, db)
    return JobResult(id=job.id, operation=job.operation, status=job.status, artifact=None, error=None)


@app.get(
    "/v1/jobs/{job_id}/artifact",
    operation_id="getVideoArtifact",
    response_class=FileResponse,
    responses={
        200: {
            "content": {
                "video/mp4": {
                    "schema": {"type": "string", "format": "binary"},
                }
            }
        }
    },
)
def get_artifact(
    job_id: str,
    actor: Actor = Depends(require_roles("viewer", "producer", "reviewer", "publisher", "auditor", "administrator", "agent_service")),
    db: Session = Depends(db_session),
):
    job = db.get(Job, job_id)
    if (not job or job.owner != actor.name or job.status != "awaiting_review"
            or not job.artifact):
        raise HTTPException(404, "Artifact not found")
    relative = Path(job.artifact)
    expected_name = "generated.mp4" if job.operation == "wan22_t2v" else "test.mp4"
    if relative.is_absolute() or relative.parts != (job.id, expected_name):
        raise HTTPException(404, "Artifact not found")
    try:
        root = ARTIFACT_ROOT.resolve(strict=True)
        job_dir = root / job.id
        artifact = job_dir / relative.name
        if (job_dir.is_symlink() or artifact.is_symlink() or not artifact.is_file()
                or not 0 < artifact.stat().st_size <= 512 * 1024 * 1024):
            raise OSError
        artifact.resolve(strict=True).relative_to(job_dir.resolve(strict=True))
    except (OSError, ValueError):
        raise HTTPException(404, "Artifact not found") from None
    db.add(Audit(job_id=job.id, actor=actor.name, action="artifact_downloaded"))
    db.commit()
    return FileResponse(artifact, media_type="video/mp4", filename="video.mp4")


@app.get("/v1/models", operation_id="listVideoModels")
def get_models(
    actor: Actor = Depends(require_roles("viewer", "producer", "reviewer", "publisher", "auditor", "administrator", "agent_service"))
):
    return {"models": [{"id": "wan22-ti2v-5b", "operation": "wan22_t2v", "status": "requires_local_gpu_profile_and_approved_workflow"}]}
