import hashlib
import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Literal
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .auth import Actor, require_roles
from .audio_workflows import (
    MAX_AUDIO_BYTES,
    MAX_CAPTION_BYTES,
    AudioWorkflowError,
    canonical_hash,
    validate_audio,
    validate_captions,
)
from .core import (
    ARTIFACT_ROOT,
    AudioAsset,
    Audit,
    DispatchOutbox,
    Job,
    MediaArtifact,
    MediaAsset,
    Project,
    SessionLocal,
    WorkflowArtifact,
    transition,
)
from .private_storage import private_file, write_private_file
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


class CreateProject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Project name is required")
        return value


class CaptionCue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_ms: StrictInt
    end_ms: StrictInt
    text: str = Field(min_length=1, max_length=500)


class CreateAudioWorkflow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workflow_version: Literal[1]
    step: Literal["narration", "transcription", "captions"]
    audio_asset_id: str | None = None
    text: str | None = Field(default=None, max_length=2000)
    language: str = Field(default="en", pattern=r"^[a-z]{2,3}(?:-[A-Z]{2})?$")
    captions: list[CaptionCue] | None = Field(default=None, max_length=1000)
    formats: list[Literal["srt", "vtt"]] = Field(
        default_factory=lambda: ["srt", "vtt"], min_length=1, max_length=2)


class JobResult(BaseModel):
    id: str
    operation: str
    status: str
    artifact: str | None
    error: str | None
    dispatch_attempts: int = 0
    dispatch_error: str | None = None


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


class AudioAssetResult(BaseModel):
    id: str
    media_type: str
    size_bytes: int
    duration_ms: int
    sha256: str


def _owned_project(db: Session, project_id: str, actor: Actor) -> Project:
    project = db.get(Project, project_id)
    if not project or project.owner != actor.name:
        raise HTTPException(404, "Project not found")
    return project


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
    project = Project(id=str(uuid.uuid4()), owner=actor.name, name=payload.name)
    db.add(project)
    db.commit()
    return ProjectResult(id=project.id, name=project.name)


@app.get("/v1/projects/{project_id}", response_model=ProjectResult)
def get_project(
    project_id: str,
    actor: Actor = Depends(require_roles("viewer", "producer", "reviewer", "publisher", "auditor", "administrator", "agent_service")),
    db: Session = Depends(db_session),
):
    project = _owned_project(db, project_id, actor)
    return ProjectResult(id=project.id, name=project.name)


@app.post("/v1/projects/{project_id}/audio", response_model=AudioAssetResult, status_code=201)
def upload_audio(
    project_id: str,
    upload: UploadFile = File(...),
    actor: Actor = Depends(require_roles("producer", "administrator", "agent_service")),
    db: Session = Depends(db_session),
):
    project = _owned_project(db, project_id, actor)
    try:
        content = upload.file.read(MAX_AUDIO_BYTES + 1)
        if len(content) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "Audio exceeds the supported size")
        metadata = validate_audio(content)
    except AudioWorkflowError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        upload.file.close()

    asset_id = str(uuid.uuid4())
    relative_path = f"projects/{project.id}/audio/{asset_id}.wav"
    try:
        write_private_file(ARTIFACT_ROOT, relative_path, content)
        asset = AudioAsset(
            id=asset_id,
            project_id=project.id,
            owner=actor.name,
            relative_path=relative_path,
            media_type="audio/wav",
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            duration_ms=metadata["duration_ms"],
        )
        db.add(asset)
        db.commit()
    except Exception as exc:
        try:
            private_file(ARTIFACT_ROOT, relative_path).unlink(missing_ok=True)
        except (FileNotFoundError, ValueError):
            pass
        raise HTTPException(503, "Audio storage is unavailable") from exc
    return AudioAssetResult(
        id=asset.id,
        media_type=asset.media_type,
        size_bytes=asset.size_bytes,
        duration_ms=asset.duration_ms,
        sha256=asset.sha256,
    )


@app.get("/v1/projects", response_model=list[ProjectResult], operation_id="listProjects")
def list_projects(
    actor: Actor = Depends(require_roles("viewer", "producer", "reviewer", "publisher", "auditor", "administrator", "agent_service")),
    db: Session = Depends(db_session),
):
    return [ProjectResult(id=project.id, name=project.name)
            for project in db.query(Project).filter(Project.owner == actor.name).all()]


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


@app.get("/v1/projects/{project_id}/audio/{asset_id}")
def get_audio(
    project_id: str,
    asset_id: str,
    actor: Actor = Depends(require_roles("viewer", "producer", "reviewer", "publisher", "auditor", "administrator", "agent_service")),
    db: Session = Depends(db_session),
):
    _owned_project(db, project_id, actor)
    asset = db.get(AudioAsset, asset_id)
    if not asset or asset.project_id != project_id or asset.owner != actor.name:
        raise HTTPException(404, "Audio asset not found")
    try:
        path = private_file(ARTIFACT_ROOT, asset.relative_path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, "Audio asset not found") from exc
    return FileResponse(path, media_type=asset.media_type, filename=f"{asset.id}.wav")


@app.post("/v1/projects/{project_id}/workflows", response_model=JobResult, status_code=202)
def create_audio_workflow(
    project_id: str,
    payload: CreateAudioWorkflow,
    actor: Actor = Depends(require_roles("producer", "administrator", "agent_service")),
    db: Session = Depends(db_session),
):
    project = _owned_project(db, project_id, actor)
    if payload.step == "narration":
        if not payload.text or not payload.text.strip() or payload.captions is not None:
            raise HTTPException(422, "Narration requires text and no caption input")
    elif payload.step == "transcription":
        if not payload.audio_asset_id or payload.text is not None or payload.captions is not None:
            raise HTTPException(422, "Transcription requires a project audio asset")
    elif payload.captions is None or payload.text is not None:
        raise HTTPException(422, "Caption export requires caption cues")
    if payload.step != "captions" and payload.formats != ["srt", "vtt"]:
        raise HTTPException(422, "Formats apply only to caption export")
    if len(set(payload.formats)) != len(payload.formats):
        raise HTTPException(422, "Subtitle formats must be unique")
    if payload.captions is not None:
        try:
            normalized_captions = validate_captions(
                [cue.model_dump() for cue in payload.captions])
        except AudioWorkflowError as exc:
            raise HTTPException(422, str(exc)) from exc
        if len(json.dumps(normalized_captions, ensure_ascii=False).encode("utf-8")) > MAX_CAPTION_BYTES:
            raise HTTPException(413, "Caption input exceeds the supported size")

    audio_asset = None
    if payload.audio_asset_id is not None:
        audio_asset = db.get(AudioAsset, payload.audio_asset_id)
        if (not audio_asset or audio_asset.project_id != project.id
                or audio_asset.owner != actor.name):
            raise HTTPException(404, "Audio asset not found")
    if payload.step == "transcription" and audio_asset is None:
        raise HTTPException(404, "Audio asset not found")

    spec = payload.model_dump(mode="json", exclude_none=True)
    input_hashes = []
    if audio_asset:
        input_hashes.append({
            "asset_id": audio_asset.id,
            "sha256": audio_asset.sha256,
            "size_bytes": audio_asset.size_bytes,
        })
    if payload.captions is not None:
        input_hashes.append({
            "kind": "caption_cues",
            "sha256": canonical_hash(normalized_captions),
        })
    if payload.text is not None:
        input_hashes.append({
            "kind": "narration_text",
            "sha256": hashlib.sha256(payload.text.encode("utf-8")).hexdigest(),
        })
    spec["input_manifest"] = input_hashes
    spec["attempt"] = 1
    job = Job(
        id=str(uuid.uuid4()),
        owner=actor.name,
        project_id=project.id,
        operation="audio_workflow",
        status="dispatch_pending",
        spec=spec,
    )
    db.add(job)
    db.flush()
    db.add(DispatchOutbox(job_id=job.id, status="pending"))
    db.add(Audit(job_id=job.id, actor=actor.name, action="dispatch_pending"))
    db.commit()
    dispatch_outbox(job.id)
    db.refresh(job)
    return _job_result(job)


@app.get("/v1/projects/{project_id}/workflows/{job_id}", response_model=JobResult)
def get_audio_workflow(
    project_id: str,
    job_id: str,
    actor: Actor = Depends(require_roles("viewer", "producer", "reviewer", "publisher", "auditor", "administrator", "agent_service")),
    db: Session = Depends(db_session),
):
    _owned_project(db, project_id, actor)
    job = db.get(Job, job_id)
    if not job or job.project_id != project_id or job.owner != actor.name:
        raise HTTPException(404, "Workflow job not found")
    return _job_result(job)


@app.post("/v1/projects/{project_id}/workflows/{job_id}/retry", response_model=JobResult, status_code=202)
def retry_audio_workflow(
    project_id: str,
    job_id: str,
    actor: Actor = Depends(require_roles("producer", "administrator", "agent_service")),
    db: Session = Depends(db_session),
):
    _owned_project(db, project_id, actor)
    original = db.get(Job, job_id)
    if (not original or original.project_id != project_id or original.owner != actor.name
            or original.operation != "audio_workflow"):
        raise HTTPException(404, "Workflow job not found")
    if original.status == "dispatch_pending":
        outbox = db.get(DispatchOutbox, original.id)
        if outbox is None:
            raise HTTPException(409, "Dispatch intent is unavailable")
        outbox.status = "pending"
        outbox.available_at = datetime.now(timezone.utc)
        outbox.lease_expires_at = None
        db.add(Audit(job_id=original.id, actor=actor.name, action="retry_requested"))
        db.commit()
        if not dispatch_outbox(original.id):
            db.refresh(original)
            raise HTTPException(503, "Workflow dispatch is unavailable; retry later")
        db.refresh(original)
        return _job_result(original)
    attempt = original.spec.get("attempt", 1)
    if original.status != "failed" or type(attempt) is not int or attempt >= 3:
        raise HTTPException(409, "Workflow is not eligible for retry")
    if db.query(Job.id).filter(Job.retry_of_id == original.id).first():
        raise HTTPException(409, "Workflow retry has already been created")
    job = Job(
        id=str(uuid.uuid4()),
        owner=original.owner,
        project_id=original.project_id,
        retry_of_job_id=original.id,
        operation=original.operation,
        status="dispatch_pending",
        spec={**original.spec, "attempt": attempt + 1},
    )
    try:
        db.add(job)
        db.flush()
        db.add(DispatchOutbox(job_id=job.id, status="pending"))
        db.add(Audit(job_id=original.id, actor=actor.name, action="retry_requested"))
        db.add(Audit(job_id=job.id, actor=actor.name, action="retry_created"))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Workflow retry has already been created") from exc
    dispatch_outbox(job.id)
    db.refresh(job)
    return _job_result(job)


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
    workflow_artifact = db.get(WorkflowArtifact, artifact_id)
    if workflow_artifact is not None:
        if (workflow_artifact.project_id != project.id
                or workflow_artifact.owner != actor.name):
            raise HTTPException(404, "Artifact not found")
        try:
            path = private_file(ARTIFACT_ROOT, workflow_artifact.relative_path)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(404, "Artifact not found") from exc
        expected_size = workflow_artifact.size_bytes
        expected_sha256 = workflow_artifact.sha256
        media_type = workflow_artifact.media_type
        filename = workflow_artifact.filename
        audit_job_id = workflow_artifact.job_id
    else:
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
        expected_size = artifact.size_bytes
        expected_sha256 = artifact.sha256
        media_type = artifact.content_type
        filename = artifact.original_filename
        audit_job_id = None

    digest = hashlib.sha256()
    with path.open("rb") as media:
        for chunk in iter(lambda: media.read(1024 * 1024), b""):
            digest.update(chunk)
    if path.stat().st_size != expected_size or digest.hexdigest() != expected_sha256:
        raise HTTPException(409, "Artifact integrity check failed")
    if audit_job_id is not None:
        db.add(Audit(job_id=audit_job_id, actor=actor.name, action="artifact_download"))
        db.commit()
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        headers={"X-Content-SHA256": expected_sha256},
    )


@app.post("/v1/jobs", response_model=JobResult, status_code=202, operation_id="submitVideoJob")
def create_job(
    payload: CreateJob,
    actor: Actor = Depends(require_roles("producer", "administrator", "agent_service")),
    db: Session = Depends(db_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if payload.operation not in {"render_test", "wan22_t2v"}:
        raise HTTPException(422, "Unsupported job operation")
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
    if job.operation == "audio_workflow":
        raise HTTPException(409, "Use the project-scoped workflow retry endpoint")
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
        try:
            db.add(retry)
            db.flush()
            db.add(DispatchOutbox(job_id=retry.id, status="pending"))
            db.add(Audit(job_id=job.id, actor=actor.name, action="retry_requested"))
            db.add(Audit(job_id=retry.id, actor=actor.name, action="retry_created"))
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(409, "Job retry has already been created") from exc
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
