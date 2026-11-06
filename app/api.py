import uuid
import secrets
from datetime import datetime, timezone
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import (
    SESSION_LIFETIME,
    AccountInput,
    Actor,
    hash_password,
    hash_session_token,
    require_roles,
    verify_password,
)
from .core import AuthSession, Audit, Job, Project, ProjectMembership, User, get_db, queue

app = FastAPI(title="OJU Video Forge", version="0.1.0", docs_url=None, redoc_url=None, openapi_url="/openapi.json")
db_session = get_db


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


class ProjectResult(BaseModel):
    id: str
    name: str
    role: str


class ProjectMemberResult(BaseModel):
    username: str
    role: str


class ProjectMemberInput(BaseModel):
    role: Literal["viewer", "editor"]


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
    actor: Actor = Depends(require_roles("viewer")),
    db: Session = Depends(db_session),
):
    if actor.user_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "A local user session is required")
    project = Project(id=str(uuid.uuid4()), name=payload.name)
    db.add(project)
    db.flush()
    db.add(ProjectMembership(project_id=project.id, user_id=actor.user_id, role="owner"))
    db.commit()
    return ProjectResult(id=project.id, name=project.name, role="owner")


@app.get("/v1/projects", response_model=list[ProjectResult], operation_id="listProjects")
def list_projects(
    actor: Actor = Depends(require_roles("viewer")),
    db: Session = Depends(db_session),
):
    if actor.user_id is None:
        return []
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


@app.get(
    "/v1/projects/{project_id}",
    response_model=ProjectResult,
    operation_id="getProject",
)
def get_project(
    project_id: str,
    actor: Actor = Depends(require_roles("viewer")),
    db: Session = Depends(db_session),
):
    project, role = require_project_role(db, actor, project_id, {"viewer", "editor", "owner"})
    return ProjectResult(id=project.id, name=project.name, role=role)


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
    if not job or actor.user_id is not None or job.owner != actor.name:
        raise HTTPException(404, "Job not found")
    return JobResult(id=job.id, operation=job.operation, status=job.status, artifact=job.artifact, error=job.error)


@app.get("/v1/models", operation_id="listVideoModels")
def get_models(
    actor: Actor = Depends(require_roles("viewer", "producer", "reviewer", "publisher", "auditor", "administrator", "agent_service"))
):
    return {"models": [{"id": "wan22-ti2v-5b", "operation": "wan22_t2v", "status": "requires_workflow_registration"}]}
