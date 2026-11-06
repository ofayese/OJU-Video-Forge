import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy import update
from sqlalchemy.orm import Session

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
    Job,
    Project,
    SessionLocal,
    WorkflowArtifact,
    queue,
)
from .private_storage import private_file, write_private_file

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


class ProjectResult(BaseModel):
    id: str
    name: str


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


def _enqueue(job: Job, db: Session) -> bool:
    if job.status == "dispatch_pending":
        claim = db.execute(
            update(Job)
            .where(Job.id == job.id, Job.status == "dispatch_pending")
            .values(status="queued", updated_at=datetime.now(timezone.utc))
        )
        if claim.rowcount != 1:
            db.rollback()
            db.refresh(job)
            return job.status != "dispatch_pending"
    db.add(Audit(job_id=job.id, actor=job.owner, action="dispatching"))
    db.commit()
    try:
        queue().enqueue("app.worker.execute", job.id, job_id=job.id, job_timeout=3600)
    except Exception:
        failed_dispatch = db.execute(
            update(Job)
            .where(Job.id == job.id, Job.status == "queued")
            .values(status="dispatch_pending", updated_at=datetime.now(timezone.utc))
        )
        if failed_dispatch.rowcount:
            db.add(Audit(job_id=job.id, actor=job.owner, action="dispatch_pending"))
        db.commit()
        db.refresh(job)
        return job.status != "dispatch_pending"
    return True


def db_session():
    with SessionLocal() as db:
        yield db


@app.get("/health/live", include_in_schema=False)
def live():
    return {"ok": True}


@app.post("/v1/projects", response_model=ProjectResult, status_code=201)
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
async def upload_audio(
    project_id: str,
    upload: UploadFile = File(...),
    actor: Actor = Depends(require_roles("producer", "administrator", "agent_service")),
    db: Session = Depends(db_session),
):
    project = _owned_project(db, project_id, actor)
    try:
        content = await upload.read(MAX_AUDIO_BYTES + 1)
        if len(content) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "Audio exceeds the supported size")
        metadata = validate_audio(content)
    except AudioWorkflowError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        await upload.close()

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
        status="queued",
        spec=spec,
    )
    db.add(job)
    db.add(Audit(job_id=job.id, actor=actor.name, action="queued"))
    db.commit()
    _enqueue(job, db)
    return JobResult(
        id=job.id, operation=job.operation, status=job.status,
        artifact=job.artifact, error=job.error,
    )


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
    return JobResult(
        id=job.id, operation=job.operation, status=job.status,
        artifact=job.artifact, error=job.error,
    )


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
        if not _enqueue(original, db):
            raise HTTPException(503, "Workflow dispatch is unavailable; retry later")
        db.refresh(original)
        return JobResult(
            id=original.id, operation=original.operation, status=original.status,
            artifact=original.artifact, error=original.error,
        )
    attempt = original.spec.get("attempt", 1)
    if original.status != "failed" or type(attempt) is not int or attempt >= 3:
        raise HTTPException(409, "Workflow is not eligible for retry")
    if db.query(Job.id).filter(Job.retry_of_id == original.id).first():
        raise HTTPException(409, "Workflow retry has already been created")
    job = Job(
        id=str(uuid.uuid4()),
        owner=original.owner,
        project_id=original.project_id,
        retry_of_id=original.id,
        operation=original.operation,
        status="queued",
        spec={**original.spec, "attempt": attempt + 1},
    )
    db.add(job)
    db.add(Audit(job_id=job.id, actor=actor.name, action="queued"))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Workflow retry has already been created") from exc
    _enqueue(job, db)
    return JobResult(
        id=job.id, operation=job.operation, status=job.status,
        artifact=job.artifact, error=job.error,
    )


@app.get("/v1/projects/{project_id}/artifacts/{artifact_id}")
def get_workflow_artifact(
    project_id: str,
    artifact_id: str,
    actor: Actor = Depends(require_roles("viewer", "producer", "reviewer", "publisher", "auditor", "administrator", "agent_service")),
    db: Session = Depends(db_session),
):
    _owned_project(db, project_id, actor)
    artifact = db.get(WorkflowArtifact, artifact_id)
    if (not artifact or artifact.project_id != project_id
            or artifact.owner != actor.name):
        raise HTTPException(404, "Artifact not found")
    try:
        path = private_file(ARTIFACT_ROOT, artifact.relative_path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, "Artifact not found") from exc
    db.add(Audit(job_id=artifact.job_id, actor=actor.name, action="artifact_download"))
    db.commit()
    return FileResponse(path, media_type=artifact.media_type, filename=artifact.filename)


@app.post("/v1/jobs", response_model=JobResult, status_code=202, operation_id="submitVideoJob")
def create_job(
    payload: CreateJob,
    actor: Actor = Depends(require_roles("producer", "administrator", "agent_service")),
    db: Session = Depends(db_session),
):
    if payload.operation not in {"render_test", "wan22_t2v"}:
        raise HTTPException(422, "Unsupported job operation")
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
        job.status = "dispatch_pending"
        db.commit()
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
    return JobResult(id=job.id, operation=job.operation, status=job.status, artifact=job.artifact, error=job.error)


@app.get("/v1/models", operation_id="listVideoModels")
def get_models(
    actor: Actor = Depends(require_roles("viewer", "producer", "reviewer", "publisher", "auditor", "administrator", "agent_service"))
):
    return {"models": [{"id": "wan22-ti2v-5b", "operation": "wan22_t2v", "status": "requires_workflow_registration"}]}
