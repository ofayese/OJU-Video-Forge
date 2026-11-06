import hashlib
import json
import os
import re
import secrets
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import (
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .agents import (
    ALLOWED_TOOLS,
    AgentUnavailable,
    get_agent_adapter,
    validate_agent_output,
)
from .auth import (
    SESSION_LIFETIME,
    AccountInput,
    Actor,
    hash_password,
    hash_session_token,
    require_roles,
    verify_password,
)
from .audio_workflows import (
    MAX_AUDIO_BYTES,
    MAX_CAPTION_BYTES,
    AudioWorkflowError,
    canonical_hash,
    validate_audio,
    validate_captions,
)
from .config import get_settings
from .core import (
    ARTIFACT_ROOT,
    AudioAsset,
    Audit,
    AuthSession,
    Job,
    AgentAudit,
    AgentProposal,
    DispatchOutbox,
    MediaArtifact,
    MediaAsset,
    Project,
    ProjectMembership,
    User,
    WorkflowArtifact,
    get_db,
    transition,
)
from .private_storage import private_file, write_private_file
from .dispatcher import dispatch_outbox

app = FastAPI(title="OJU Video Forge", version="0.1.0", docs_url=None, redoc_url=None, openapi_url="/openapi.json")
db_session = get_db

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
    operation: Literal["render_test", "wan22_t2v", "render_timeline_v1"]
    project_id: str | None = None
    media_id: str | None = None
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
    artifact: str | None = Field(description="Artifact ID for audio workflows or authenticated video download URL when ready")
    error: str | None
    dispatch_attempts: int = 0
    dispatch_error: str | None = None


class AgentTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: str = Field(min_length=1, max_length=4000)


class AgentProposalResult(BaseModel):
    id: str
    project_id: str
    tool: str
    status: str


class AgentDecisionResult(BaseModel):
    id: str
    status: str
    job: JobResult | None = None


class ProjectResult(BaseModel):
    id: str
    name: str
    role: str | None = None


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
    if project is None:
        raise HTTPException(404, "Project not found")
    if actor.user_id is not None:
        membership = db.scalar(
            select(ProjectMembership.role).where(
                ProjectMembership.project_id == project_id,
                ProjectMembership.user_id == actor.user_id,
            )
        )
        if membership is None:
            raise HTTPException(404, "Project not found")
    elif (
        project.owner != actor.name
        or db.scalar(
            select(ProjectMembership.id).where(
                ProjectMembership.project_id == project_id
            )
        )
        is not None
    ):
        raise HTTPException(404, "Project not found")
    return project


class UserResult(BaseModel):
    id: str
    username: str


class AuthSessionResult(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserResult


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=128)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Project name cannot be blank.")
        return normalized


class ProjectMemberResult(BaseModel):
    username: str
    role: str


class ProjectMemberInput(BaseModel):
    role: Literal["viewer", "editor", "reviewer"]


def require_project_role(
    db: Session,
    actor: Actor,
    project_id: str,
    allowed_roles: set[str],
) -> tuple[Project, str]:
    if actor.user_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    row = db.execute(
        select(Project, ProjectMembership.role)
        .join(ProjectMembership, ProjectMembership.project_id == Project.id)
        .where(
            Project.id == project_id,
            ProjectMembership.user_id == actor.user_id,
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    project, role = row
    if role not in allowed_roles:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient project permissions")
    return project, role


def _is_project_membership_conflict(exc: IntegrityError) -> bool:
    original = exc.orig
    if (
        getattr(getattr(original, "diag", None), "constraint_name", None)
        == "uq_project_memberships_project_user"
    ):
        return True
    return (
        getattr(original, "sqlite_errorcode", None) == sqlite3.SQLITE_CONSTRAINT_UNIQUE
        and str(original)
        == "UNIQUE constraint failed: project_memberships.project_id, project_memberships.user_id"
    )


def _artifact_url(job: Job) -> str | None:
    if job.operation == "audio_workflow":
        return job.artifact
    return f"/v1/jobs/{job.id}/artifact" if job.status == "awaiting_review" and job.artifact else None


def reject_agent_service(actor: Actor) -> None:
    if "agent_service" in actor.roles:
        raise HTTPException(403, "Human user required")


@app.get("/health/live", include_in_schema=False)
def live():
    return {"ok": True}


@app.post(
    "/v1/auth/register",
    response_model=UserResult,
    status_code=status.HTTP_201_CREATED,
    operation_id="registerLocalUser",
)
def register_user(payload: AccountInput, db: Session = Depends(db_session)):
    user = User(
        id=str(uuid.uuid4()),
        username=payload.username,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if db.scalar(select(User.id).where(User.username == payload.username)) is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Username is already registered") from exc
        raise
    return UserResult(id=user.id, username=user.username)


@app.post(
    "/v1/auth/session",
    response_model=AuthSessionResult,
    operation_id="createLocalSession",
)
def create_session(payload: AccountInput, db: Session = Depends(db_session)):
    user = db.scalar(select(User).where(User.username == payload.username))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + SESSION_LIFETIME
    session = AuthSession(
        id=str(uuid.uuid4()),
        user_id=user.id,
        token_hash=hash_session_token(token),
        expires_at=expires_at,
    )
    db.add(session)
    db.commit()
    return AuthSessionResult(
        access_token=token,
        expires_at=expires_at,
        user=UserResult(id=user.id, username=user.username),
    )


@app.get("/v1/auth/session", response_model=UserResult, operation_id="getLocalSession")
def get_session(actor: Actor = Depends(require_roles("viewer"))):
    if actor.user_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "A local user session is required")
    return UserResult(id=actor.user_id, username=actor.name)


@app.delete("/v1/auth/session", status_code=status.HTTP_204_NO_CONTENT, operation_id="revokeLocalSession")
def revoke_session(
    actor: Actor = Depends(require_roles("viewer")),
    db: Session = Depends(db_session),
):
    if actor.user_id is None or actor.session_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "A local user session is required")
    session = db.get(AuthSession, actor.session_id)
    if session is not None and session.revoked_at is None:
        session.revoked_at = datetime.now(timezone.utc)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post(
    "/v1/projects",
    response_model=ProjectResult,
    status_code=status.HTTP_201_CREATED,
    operation_id="createProject",
)
def create_project(
    payload: ProjectInput,
    actor: Actor = Depends(
        require_roles(
            "viewer",
            "producer",
            "administrator",
            "agent_service",
        )
    ),
    db: Session = Depends(db_session),
):
    reject_agent_service(actor)
    if actor.user_id is None and not {"producer", "administrator", "agent_service"}.intersection(actor.roles):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
    project = Project(id=str(uuid.uuid4()), owner=actor.name, name=payload.name)
    db.add(project)
    if actor.user_id is not None:
        db.flush()
        db.add(
            ProjectMembership(
                project_id=project.id,
                user_id=actor.user_id,
                role="owner",
            )
        )
    db.commit()
    return ProjectResult(
        id=project.id,
        name=project.name,
        role="owner" if actor.user_id is not None else None,
    )


@app.get(
    "/v1/projects/{project_id}",
    response_model=ProjectResult,
    operation_id="getProject",
)
def get_project(
    project_id: str,
    actor: Actor = Depends(
        require_roles(
            "viewer",
            "producer",
            "reviewer",
            "publisher",
            "auditor",
            "administrator",
            "agent_service",
        )
    ),
    db: Session = Depends(db_session),
):
    project = _owned_project(db, project_id, actor)
    role = None
    if actor.user_id is not None:
        role = db.scalar(
            select(ProjectMembership.role).where(
                ProjectMembership.project_id == project_id,
                ProjectMembership.user_id == actor.user_id,
            )
        )
    return ProjectResult(id=project.id, name=project.name, role=role)


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
    actor: Actor = Depends(
        require_roles(
            "viewer",
            "producer",
            "reviewer",
            "publisher",
            "auditor",
            "administrator",
            "agent_service",
        )
    ),
    db: Session = Depends(db_session),
):
    if actor.user_id is None:
        rows = db.scalars(
            select(Project)
            .where(Project.owner == actor.name)
            .where(
                ~select(ProjectMembership.id)
                .where(ProjectMembership.project_id == Project.id)
                .exists()
            )
            .order_by(Project.created_at.desc(), Project.id)
        )
        return [ProjectResult(id=project.id, name=project.name) for project in rows]
    rows = db.execute(
        select(Project, ProjectMembership.role)
        .join(ProjectMembership, ProjectMembership.project_id == Project.id)
        .where(ProjectMembership.user_id == actor.user_id)
        .order_by(Project.created_at.desc(), Project.id)
    )
    return [
        ProjectResult(id=project.id, name=project.name, role=role)
        for project, role in rows
    ]


@app.patch(
    "/v1/projects/{project_id}",
    response_model=ProjectResult,
    operation_id="updateProject",
)
def update_project(
    project_id: str,
    payload: ProjectInput,
    actor: Actor = Depends(require_roles("viewer")),
    db: Session = Depends(db_session),
):
    project, role = require_project_role(db, actor, project_id, {"editor", "owner"})
    project.name = payload.name
    db.commit()
    return ProjectResult(id=project.id, name=project.name, role=role)


@app.get(
    "/v1/projects/{project_id}/members",
    response_model=list[ProjectMemberResult],
    operation_id="listProjectMembers",
)
def list_project_members(
    project_id: str,
    actor: Actor = Depends(require_roles("viewer")),
    db: Session = Depends(db_session),
):
    require_project_role(db, actor, project_id, {"viewer", "editor", "owner"})
    rows = db.execute(
        select(User.username, ProjectMembership.role)
        .join(ProjectMembership, ProjectMembership.user_id == User.id)
        .where(ProjectMembership.project_id == project_id)
        .order_by(User.username)
    )
    return [ProjectMemberResult(username=username, role=role) for username, role in rows]


@app.put(
    "/v1/projects/{project_id}/members/{username}",
    response_model=ProjectMemberResult,
    operation_id="setProjectMember",
)
def set_project_member(
    project_id: str,
    username: str,
    payload: ProjectMemberInput,
    actor: Actor = Depends(require_roles("viewer")),
    db: Session = Depends(db_session),
):
    require_project_role(db, actor, project_id, {"owner"})
    user = db.scalar(select(User).where(User.username == username.casefold()))
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    membership = db.scalar(
        select(ProjectMembership).where(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == user.id,
        )
    )
    if membership is None:
        membership = ProjectMembership(
            project_id=project_id,
            user_id=user.id,
            role=payload.role,
        )
        db.add(membership)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            if not _is_project_membership_conflict(exc):
                raise
            membership = db.scalar(
                select(ProjectMembership).where(
                    ProjectMembership.project_id == project_id,
                    ProjectMembership.user_id == user.id,
                )
            )
            if membership is None:
                raise
            if membership.role == "owner":
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "The project owner role cannot be changed",
                ) from exc
            membership.role = payload.role
            db.commit()
    else:
        if membership.role == "owner":
            raise HTTPException(status.HTTP_409_CONFLICT, "The project owner role cannot be changed")
        membership.role = payload.role
        db.commit()
    return ProjectMemberResult(username=user.username, role=membership.role)


@app.delete(
    "/v1/projects/{project_id}/members/{username}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="removeProjectMember",
)
def remove_project_member(
    project_id: str,
    username: str,
    actor: Actor = Depends(require_roles("viewer")),
    db: Session = Depends(db_session),
):
    require_project_role(db, actor, project_id, {"owner"})
    membership = db.scalar(
        select(ProjectMembership)
        .join(User, ProjectMembership.user_id == User.id)
        .where(
            ProjectMembership.project_id == project_id,
            User.username == username.casefold(),
        )
    )
    if membership is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project member not found")
    if membership.role == "owner":
        raise HTTPException(status.HTTP_409_CONFLICT, "The project owner cannot be removed")
    db.delete(membership)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
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
    if (
        actor.user_id is not None
        or not job
        or job.project_id != project_id
        or job.owner != actor.name
    ):
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
                for content_type in (*MEDIA_TYPES.values(), "application/json", "text/vtt", "application/x-subrip")
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
    reject_agent_service(actor)
    if payload.operation not in {"render_test", "wan22_t2v"}:
        raise HTTPException(422, "Unsupported job operation")
    if payload.operation == "wan22_t2v" and not get_settings().comfyui_enabled:
        raise HTTPException(503, "ComfyUI generation is disabled")
    if payload.operation == "wan22_t2v" and not payload.prompt.strip():
        raise HTTPException(422, "Prompt required for AI generation")
    if idempotency_key is not None and not 1 <= len(idempotency_key) <= 128:
        raise HTTPException(422, "Invalid Idempotency-Key")
    if payload.media_id and not payload.project_id:
        raise HTTPException(422, "Project required for media jobs")
    if payload.project_id:
        _owned_project(db, payload.project_id, actor)
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
    if not job or actor.user_id is not None or job.owner != actor.name:
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
    original = db.execute(
        select(Job).where(Job.id == job_id).with_for_update()
    ).scalar_one_or_none()
    if not original or original.owner != actor.name:
        raise HTTPException(404, "Job not found")
    if original.operation == "audio_workflow":
        raise HTTPException(409, "Use the project-scoped workflow retry endpoint")
    if original.operation == "wan22_t2v" and not get_settings().comfyui_enabled:
        raise HTTPException(503, "ComfyUI generation is disabled")
    if original.status == "dispatch_pending":
        outbox = db.get(DispatchOutbox, job_id)
        if outbox is None:
            raise HTTPException(409, "Dispatch intent is unavailable")
        outbox.status = "pending"
        outbox.available_at = datetime.now(timezone.utc)
        outbox.lease_expires_at = None
        db.add(
            Audit(
                job_id=original.id,
                actor=actor.name,
                action="dispatch_retry_requested",
            )
        )
        db.commit()
        dispatch_outbox(original.id)
        db.refresh(original)
        return _job_result(original)
    if original.status != "failed":
        raise HTTPException(409, "Only dispatch-pending or failed jobs can be retried")

    if not isinstance(original.spec, dict):
        raise HTTPException(409, "Retry history is invalid")
    root_id = original.spec.get("_retry_root")
    if root_id is None:
        root = original
        while root.retry_of_job_id:
            parent = db.get(Job, root.retry_of_job_id)
            if parent is None or parent.owner != actor.name:
                raise HTTPException(409, "Retry history is invalid")
            root = parent
        root_id = root.id
    if not isinstance(root_id, str):
        raise HTTPException(409, "Retry history is invalid")
    root = db.execute(
        select(Job).where(Job.id == root_id).with_for_update()
    ).scalar_one_or_none()
    if not root or root.owner != actor.name:
        raise HTTPException(409, "Retry history is invalid")
    retry_count = db.query(Audit).filter_by(
        job_id=root_id, action="retry_requested"
    ).count()
    if retry_count >= 3:
        raise HTTPException(409, "Retry limit reached")

    retry = Job(
        id=str(uuid.uuid4()),
        owner=original.owner,
        project_id=original.project_id,
        media_id=original.media_id,
        retry_of_job_id=original.id,
        operation=original.operation,
        status="dispatch_pending",
        spec={**original.spec, "_retry_root": root_id, "_retry_of": original.id},
    )
    try:
        db.add(retry)
        db.flush()
        db.add(DispatchOutbox(job_id=retry.id, status="pending"))
        db.add(Audit(job_id=root_id, actor=actor.name, action="retry_requested"))
        db.add(Audit(job_id=retry.id, actor=actor.name, action="retry_created"))
        db.add(Audit(job_id=retry.id, actor=actor.name, action=f"retry_of:{original.id}"))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Job retry has already been created") from exc
    dispatch_outbox(retry.id)
    db.refresh(retry)
    return _job_result(retry)


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
    if (not job or actor.user_id is not None or job.owner != actor.name or job.status != "awaiting_review"
            or not job.artifact):
        raise HTTPException(404, "Artifact not found")
    relative = Path(job.artifact)
    valid_path = (
        not relative.is_absolute()
        and relative.parts
        and all(part not in {"", ".", ".."} for part in relative.parts)
        and relative.parts[0] == job.id
    )
    if job.operation == "wan22_t2v":
        valid_path = valid_path and relative.parts == (job.id, "generated.mp4")
    elif job.operation == "render_test":
        valid_path = valid_path and relative.parts == (job.id, "test.mp4")
    elif job.operation == "render_timeline_v1":
        valid_path = (
            valid_path
            and len(relative.parts) == 4
            and relative.parts[1] == "inputs"
            and re.fullmatch(r"render-[A-Za-z0-9_-]+", relative.parts[2]) is not None
            and relative.parts[3] == "output.mp4"
        )
    else:
        valid_path = False
    if not valid_path:
        raise HTTPException(404, "Artifact not found")
    try:
        root = ARTIFACT_ROOT.resolve(strict=True)
        job_dir = root / job.id
        artifact = root.joinpath(*relative.parts)
        candidates = [
            job_dir,
            *[root.joinpath(*relative.parts[:index])
              for index in range(2, len(relative.parts) + 1)],
        ]
        if (any(path.is_symlink() for path in candidates)
                or not artifact.is_file()
                or not 0 < artifact.stat().st_size <= 512 * 1024 * 1024):
            raise OSError
        artifact.resolve(strict=True).relative_to(job_dir.resolve(strict=True))
    except (OSError, ValueError):
        raise HTTPException(404, "Artifact not found") from None
    db.add(Audit(job_id=job.id, actor=actor.name, action="artifact_downloaded"))
    db.commit()
    return FileResponse(artifact, media_type="video/mp4", filename="video.mp4")


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
        artifact=_artifact_url(job),
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
    return {"models": [{"id": "wan22-ti2v-5b", "operation": "wan22_t2v", "status": "requires_local_gpu_profile_and_approved_workflow"}]}


@app.post(
    "/v1/projects/{project_id}/agent/proposals",
    response_model=AgentProposalResult,
    status_code=201,
)
def create_agent_proposal(
    project_id: str,
    payload: AgentTask,
    actor: Actor = Depends(require_roles("viewer", "producer", "administrator")),
    db: Session = Depends(db_session),
):
    reject_agent_service(actor)
    if not get_settings().agents_enabled:
        raise HTTPException(503, "Agent adapter is disabled")
    _, project_role = require_project_role(db, actor, project_id, {"owner", "editor"})
    if project_role not in {"owner", "editor"}:
        raise HTTPException(403, "Project permission denied")
    try:
        raw_output = get_agent_adapter().propose(payload.task, ALLOWED_TOOLS)
    except AgentUnavailable:
        raise HTTPException(503, "Agent adapter is unavailable") from None
    except Exception:
        raise HTTPException(502, "Agent proposal failed") from None
    try:
        output = validate_agent_output(raw_output)
    except ValueError:
        raise HTTPException(422, "Malformed agent output") from None

    call = output.tool_calls[0]
    proposal = AgentProposal(
        id=str(uuid.uuid4()),
        project_id=project_id,
        proposer=actor.name,
        tool_name=call.name,
        arguments=call.arguments.model_dump(),
        status="pending",
    )
    db.add(proposal)
    db.flush()
    db.add(
        AgentAudit(
            project_id=project_id,
            proposal_id=proposal.id,
            actor=actor.name,
            action="proposed",
        )
    )
    db.commit()
    return AgentProposalResult(
        id=proposal.id,
        project_id=project_id,
        tool=proposal.tool_name,
        status=proposal.status,
    )


def resolve_agent_proposal(
    project_id: str,
    proposal_id: str,
    actor: Actor,
    db: Session,
    decision: str,
) -> AgentDecisionResult:
    if "agent_service" in actor.roles:
        raise HTTPException(403, "Human approval required")
    require_project_role(db, actor, project_id, {"owner", "reviewer"})
    proposal = db.get(AgentProposal, proposal_id)
    if not proposal or proposal.project_id != project_id:
        raise HTTPException(404, "Proposal not found")
    if proposal.status != "pending":
        raise HTTPException(409, "Proposal is no longer pending")
    if decision == "approved" and proposal.proposer == actor.name:
        raise HTTPException(403, "Proposers cannot approve their own proposals")

    job_result = None
    job_id = None
    if decision == "approved":
        try:
            output = validate_agent_output(
                {
                    "tool_calls": [
                        {"name": proposal.tool_name, "arguments": proposal.arguments}
                    ]
                }
            )
        except ValueError:
            raise HTTPException(409, "Proposal is no longer valid") from None
        job = Job(
            id=str(uuid.uuid4()),
            project_id=project_id,
            owner=proposal.proposer,
            operation=output.tool_calls[0].arguments.operation,
            status="dispatch_pending",
            spec={"operation": output.tool_calls[0].arguments.operation},
        )
        db.add(job)
        db.flush()
        db.add(DispatchOutbox(job_id=job.id, status="pending"))
        db.add(Audit(job_id=job.id, actor=actor.name, action="dispatch_pending"))
        job_id = job.id
    transition_result = db.execute(
        update(AgentProposal)
        .where(
            AgentProposal.id == proposal.id,
            AgentProposal.project_id == project_id,
            AgentProposal.status == "pending",
        )
        .values(
            status=decision,
            approver=actor.name,
            resolved_at=datetime.now(timezone.utc),
            job_id=job_id,
        )
    )
    if transition_result.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "Proposal is no longer pending")
    db.add(
        AgentAudit(
            project_id=project_id,
            proposal_id=proposal.id,
            job_id=job_id,
            actor=actor.name,
            action=decision,
        )
    )
    db.commit()

    if job_id:
        if not dispatch_outbox(job_id):
            db.add(
                AgentAudit(
                    project_id=project_id,
                    proposal_id=proposal.id,
                    job_id=job_id,
                    actor="dispatcher",
                    action="dispatch_pending",
                )
            )
            db.commit()
        job = db.get(Job, job_id)
        db.refresh(job)
        job_result = _job_result(job)
    return AgentDecisionResult(id=proposal.id, status=decision, job=job_result)


@app.post(
    "/v1/projects/{project_id}/agent/proposals/{proposal_id}/approve",
    response_model=AgentDecisionResult,
)
def approve_agent_proposal(
    project_id: str,
    proposal_id: str,
    actor: Actor = Depends(require_roles("viewer", "producer", "reviewer", "administrator")),
    db: Session = Depends(db_session),
):
    return resolve_agent_proposal(project_id, proposal_id, actor, db, "approved")


@app.post(
    "/v1/projects/{project_id}/agent/proposals/{proposal_id}/reject",
    response_model=AgentDecisionResult,
)
def reject_agent_proposal(
    project_id: str,
    proposal_id: str,
    actor: Actor = Depends(require_roles("viewer", "producer", "reviewer", "administrator")),
    db: Session = Depends(db_session),
):
    return resolve_agent_proposal(project_id, proposal_id, actor, db, "rejected")


@app.post(
    "/v1/projects/{project_id}/agent/proposals/{proposal_id}/redispatch",
    response_model=AgentDecisionResult,
)
def redispatch_agent_job(
    project_id: str,
    proposal_id: str,
    actor: Actor = Depends(require_roles("viewer", "producer", "reviewer", "administrator")),
    db: Session = Depends(db_session),
):
    reject_agent_service(actor)
    require_project_role(db, actor, project_id, {"owner", "reviewer"})
    proposal = db.get(AgentProposal, proposal_id)
    if not proposal or proposal.project_id != project_id:
        raise HTTPException(404, "Proposal not found")
    if proposal.status != "approved" or not proposal.job_id:
        raise HTTPException(409, "Proposal has no approved job to redispatch")
    job = db.get(Job, proposal.job_id)
    if not job or job.project_id != project_id:
        raise HTTPException(409, "Approved job not found")
    if job.status != "dispatch_pending":
        raise HTTPException(409, "Job is not awaiting dispatch")

    outbox = db.get(DispatchOutbox, job.id)
    if outbox is None:
        raise HTTPException(409, "Dispatch intent is unavailable")
    outbox.status = "pending"
    outbox.available_at = datetime.now(timezone.utc)
    outbox.lease_expires_at = None
    db.add(Audit(job_id=job.id, actor=actor.name, action="dispatch_retry_requested"))
    db.add(
        AgentAudit(
            project_id=project_id,
            proposal_id=proposal.id,
            job_id=job.id,
            actor=actor.name,
            action="redispatch_requested",
        )
    )
    db.commit()
    if dispatch_outbox(job.id):
        db.refresh(job)
        db.add(
            AgentAudit(
                project_id=project_id,
                proposal_id=proposal.id,
                job_id=job.id,
                actor=actor.name,
                action="redispatched",
            )
        )
        db.commit()
    else:
        db.add(
            AgentAudit(
                project_id=project_id,
                proposal_id=proposal.id,
                job_id=job.id,
                actor="dispatcher",
                action="dispatch_pending",
            )
        )
        db.commit()
    db.refresh(job)

    return AgentDecisionResult(
        id=proposal.id,
        status=proposal.status,
        job=JobResult(
            id=job.id,
            operation=job.operation,
            status=job.status,
            artifact=job.artifact,
            error=job.error,
        ),
    )
    return {"models": [{"id": "wan22-ti2v-5b", "operation": "wan22_t2v", "status": "requires_local_gpu_profile_and_approved_workflow"}]}
