import hashlib
import os
import re
import tempfile
import uuid
from pathlib import Path

from datetime import datetime, timezone

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .auth import Actor, require_roles
from .core import (
    ARTIFACT_ROOT,
    Audit,
    DispatchOutbox,
    Job,
    MediaArtifact,
    MediaAsset,
    Project,
    SessionLocal,
    transition,
)
from .dispatcher import dispatch_outbox

app = FastAPI(title="OJU Video Forge", version="0.1.0", docs_url=None, redoc_url=None, openapi_url="/openapi.json")

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_MULTIPART_OVERHEAD_BYTES = 1024 * 1024
MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
}


class UploadLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or scope["method"] != "POST"
                or not re.fullmatch(r"/v1/projects/[^/]+/media", scope["path"])):
            await self.app(scope, receive, send)
            return

        limit = MAX_UPLOAD_BYTES + MAX_MULTIPART_OVERHEAD_BYTES
        headers = dict(scope.get("headers", []))
        try:
            if int(headers.get(b"content-length", b"0")) > limit:
                await self._too_large(send)
                return
        except ValueError:
            await self._too_large(send)
            return

        body = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)
        total = 0
        try:
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                total += len(chunk)
                if total > limit:
                    await self._too_large(send)
                    return
                await run_in_threadpool(body.write, chunk)
                if not message.get("more_body", False):
                    break

            await run_in_threadpool(body.seek, 0)
            finished = False

            async def replay():
                nonlocal finished
                if finished:
                    return {"type": "http.disconnect"}
                chunk, position = await run_in_threadpool(
                    lambda: (body.read(65536), body.tell())
                )
                if not chunk:
                    finished = True
                    return {"type": "http.request", "body": b"", "more_body": False}
                return {"type": "http.request", "body": chunk, "more_body": position < total}

            await self.app(scope, replay, send)
        finally:
            await run_in_threadpool(body.close)

    @staticmethod
    async def _too_large(send):
        body = b'{"detail":"Upload request exceeds the configured size limit"}'
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})


app.add_middleware(UploadLimitMiddleware)


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


class CreateProject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)


class ProjectResult(BaseModel):
    id: str
    name: str


class MediaArtifactManifest(BaseModel):
    id: str
    project_id: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str


def db_session():
    with SessionLocal() as db:
        yield db


@app.get("/health/live", include_in_schema=False)
def live():
    return {"ok": True}


@app.post("/v1/projects", response_model=ProjectResult, status_code=201, operation_id="createProject")
def create_project(
    payload: CreateProject,
    actor: Actor = Depends(require_roles("producer", "administrator", "agent_service")),
    db: Session = Depends(db_session),
):
    project = Project(id=str(uuid.uuid4()), owner=actor.name, name=payload.name.strip())
    if not project.name:
        raise HTTPException(422, "Project name must not be blank")
    db.add(project)
    db.commit()
    return ProjectResult(id=project.id, name=project.name)


@app.get("/v1/projects", response_model=list[ProjectResult], operation_id="listProjects")
def list_projects(
    actor: Actor = Depends(require_roles("viewer", "producer", "reviewer", "publisher", "auditor", "administrator", "agent_service")),
    db: Session = Depends(db_session),
):
    return [ProjectResult(id=project.id, name=project.name)
            for project in db.query(Project).filter(Project.owner == actor.name).all()]


def _owned_project(db: Session, project_id: str, actor: Actor) -> Project:
    project = db.get(Project, project_id)
    if not project or project.owner != actor.name:
        raise HTTPException(404, "Project not found")
    return project


def _private_media_directory(project_id: str) -> tuple[Path, Path]:
    root = ARTIFACT_ROOT
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root = root.resolve(strict=True)
    root.chmod(0o700)
    directory = root / "projects" / project_id / "media"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    resolved = directory.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_dir():
        raise HTTPException(500, "Private media storage is unavailable")
    for path in (root / "projects", root / "projects" / project_id, resolved):
        path.chmod(0o700)
    return root, resolved


def _manifest(artifact: MediaArtifact) -> MediaArtifactManifest:
    return MediaArtifactManifest(
        id=artifact.id,
        project_id=artifact.project_id,
        filename=artifact.original_filename,
        content_type=artifact.content_type,
        size_bytes=artifact.size_bytes,
        sha256=artifact.sha256,
    )


@app.post(
    "/v1/projects/{project_id}/media",
    response_model=MediaArtifactManifest,
    status_code=201,
    operation_id="uploadProjectMedia",
)
def upload_project_media(
    project_id: str,
    file: UploadFile = File(),
    actor: Actor = Depends(require_roles("producer", "administrator", "agent_service")),
    db: Session = Depends(db_session),
):
    project = _owned_project(db, project_id, actor)
    filename = file.filename or ""
    if (len(filename) > 255 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]*", filename)
            or filename in {".", ".."} or ".." in filename
            or "/" in filename or "\\" in filename):
        raise HTTPException(400, "Invalid media filename")
    suffix = Path(filename).suffix.lower()
    content_type = MEDIA_TYPES.get(suffix)
    if not content_type or file.content_type != content_type:
        raise HTTPException(415, "Unsupported media type")

    root, directory = _private_media_directory(project.id)
    artifact_id = str(uuid.uuid4())
    destination = directory / f"{artifact_id}{suffix}"
    digest = hashlib.sha256()
    size = 0
    created = False
    try:
        with destination.open("xb") as output:
            created = True
            os.chmod(destination, 0o600)
            while chunk := file.file.read(65536):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "Media exceeds the configured size limit")
                output.write(chunk)
                digest.update(chunk)
        if size == 0:
            raise HTTPException(400, "Media file must not be empty")

        storage_key = destination.relative_to(root).as_posix()
        artifact = MediaArtifact(
            id=artifact_id,
            project_id=project.id,
            original_filename=filename,
            content_type=content_type,
            size_bytes=size,
            sha256=digest.hexdigest(),
            storage_key=storage_key,
        )
        db.add(artifact)
        db.add(
            MediaAsset(
                id=artifact_id,
                project_id=project.id,
                filename=filename,
                media_type=content_type,
                size_bytes=size,
                sha256=digest.hexdigest(),
                storage_key=storage_key,
            )
        )
        db.commit()
        return _manifest(artifact)
    except BaseException:
        db.rollback()
        if created:
            destination.unlink(missing_ok=True)
        raise


@app.get(
    "/v1/projects/{project_id}/artifacts/{artifact_id}",
    operation_id="getProjectArtifact",
    response_class=FileResponse,
    responses={
        200: {
            "content": {
                content_type: {"schema": {"type": "string", "format": "binary"}}
                for content_type in MEDIA_TYPES.values()
            }
        }
    },
)
def get_project_artifact(
    project_id: str,
    artifact_id: str,
    actor: Actor = Depends(require_roles("viewer", "producer", "reviewer", "publisher", "auditor", "administrator", "agent_service")),
    db: Session = Depends(db_session),
):
    project = _owned_project(db, project_id, actor)
    artifact = db.get(MediaArtifact, artifact_id)
    if not artifact or artifact.project_id != project.id:
        raise HTTPException(404, "Artifact not found")

    try:
        root = ARTIFACT_ROOT.resolve(strict=True)
        path = (root / artifact.storage_key).resolve(strict=True)
    except OSError as exc:
        raise HTTPException(404, "Artifact not found") from exc
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "Artifact not found")
    digest = hashlib.sha256()
    with path.open("rb") as media:
        for chunk in iter(lambda: media.read(1024 * 1024), b""):
            digest.update(chunk)
    if path.stat().st_size != artifact.size_bytes or digest.hexdigest() != artifact.sha256:
        raise HTTPException(409, "Artifact integrity check failed")

    return FileResponse(
        path,
        media_type=artifact.content_type,
        filename=artifact.original_filename,
        headers={"X-Content-SHA256": artifact.sha256},
    )


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
