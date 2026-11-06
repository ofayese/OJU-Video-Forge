import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .agents import (
    ALLOWED_TOOLS,
    AgentUnavailable,
    get_agent_adapter,
    validate_agent_output,
)
from .auth import Actor, require_roles
from .config import get_settings
from .core import AgentAudit, AgentProposal, Audit, Job, Project, ProjectMembership, SessionLocal, queue

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


class CreateProject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)


class ProjectResult(BaseModel):
    id: str
    name: str


class ProjectMemberCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(min_length=1, max_length=128)
    role: Literal["editor", "reviewer"]


class ProjectMemberResult(BaseModel):
    actor: str
    role: str


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


def db_session():
    with SessionLocal() as db:
        yield db


def project_membership(db: Session, project_id: str, actor: Actor) -> ProjectMembership:
    project = db.get(Project, project_id)
    membership = db.get(ProjectMembership, (project_id, actor.name))
    if not project or project.archived or not membership:
        raise HTTPException(404, "Project not found")
    return membership


def reject_agent_service(actor: Actor) -> None:
    if "agent_service" in actor.roles:
        raise HTTPException(403, "Human user required")


@app.get("/health/live", include_in_schema=False)
def live():
    return {"ok": True}


@app.post("/v1/jobs", response_model=JobResult, status_code=202, operation_id="submitVideoJob")
def create_job(
    payload: CreateJob,
    actor: Actor = Depends(require_roles("producer", "administrator", "agent_service")),
    db: Session = Depends(db_session),
):
    reject_agent_service(actor)
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


@app.post("/v1/projects", response_model=ProjectResult, status_code=201)
def create_project(
    payload: CreateProject,
    actor: Actor = Depends(require_roles("producer", "administrator")),
    db: Session = Depends(db_session),
):
    reject_agent_service(actor)
    project = Project(id=str(uuid.uuid4()), name=payload.name.strip(), created_by=actor.name)
    if not project.name:
        raise HTTPException(422, "Project name is required")
    db.add(project)
    db.add(ProjectMembership(project_id=project.id, actor=actor.name, role="owner"))
    db.commit()
    return ProjectResult(id=project.id, name=project.name)


@app.put(
    "/v1/projects/{project_id}/members",
    response_model=ProjectMemberResult,
)
def add_project_member(
    project_id: str,
    payload: ProjectMemberCreate,
    actor: Actor = Depends(require_roles("producer", "administrator")),
    db: Session = Depends(db_session),
):
    reject_agent_service(actor)
    membership = project_membership(db, project_id, actor)
    if membership.role != "owner":
        raise HTTPException(403, "Project owner required")
    if payload.actor == actor.name:
        raise HTTPException(422, "Cannot change your own project role")
    member = db.get(ProjectMembership, (project_id, payload.actor))
    if member:
        member.role = payload.role
    else:
        member = ProjectMembership(
            project_id=project_id,
            actor=payload.actor,
            role=payload.role,
        )
        db.add(member)
    db.commit()
    return ProjectMemberResult(actor=member.actor, role=member.role)


@app.get("/v1/projects", response_model=list[ProjectResult])
def list_projects(
    actor: Actor = Depends(require_roles("viewer", "producer", "reviewer", "administrator")),
    db: Session = Depends(db_session),
):
    projects = db.scalars(
        select(Project)
        .join(ProjectMembership, ProjectMembership.project_id == Project.id)
        .where(ProjectMembership.actor == actor.name, Project.archived.is_(False))
    )
    return [ProjectResult(id=project.id, name=project.name) for project in projects]


@app.post(
    "/v1/projects/{project_id}/agent/proposals",
    response_model=AgentProposalResult,
    status_code=201,
)
def create_agent_proposal(
    project_id: str,
    payload: AgentTask,
    actor: Actor = Depends(require_roles("producer", "administrator")),
    db: Session = Depends(db_session),
):
    reject_agent_service(actor)
    if not get_settings().agents_enabled:
        raise HTTPException(503, "Agent adapter is disabled")
    membership = project_membership(db, project_id, actor)
    if membership.role not in {"owner", "editor"}:
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
    membership = project_membership(db, project_id, actor)
    if membership.role not in {"owner", "reviewer"}:
        raise HTTPException(403, "Project permission denied")
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
            status="queued",
            spec={"operation": output.tool_calls[0].arguments.operation},
        )
        db.add(job)
        job_id = job.id
        job_result = JobResult(
            id=job.id,
            operation=job.operation,
            status=job.status,
            artifact=None,
            error=None,
        )
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
        try:
            queue().enqueue("app.worker.execute", job_id, job_id=job_id, job_timeout=3600)
        except Exception:
            job = db.get(Job, job_id)
            job.status = "dispatch_pending"
            db.add(
                AgentAudit(
                    project_id=project_id,
                    proposal_id=proposal.id,
                    job_id=job_id,
                    actor="system",
                    action="dispatch_pending",
                )
            )
            db.commit()
            job_result = JobResult(
                id=job.id,
                operation=job.operation,
                status=job.status,
                artifact=None,
                error=None,
            )
    return AgentDecisionResult(id=proposal.id, status=proposal.status, job=job_result)


@app.post(
    "/v1/projects/{project_id}/agent/proposals/{proposal_id}/approve",
    response_model=AgentDecisionResult,
)
def approve_agent_proposal(
    project_id: str,
    proposal_id: str,
    actor: Actor = Depends(require_roles("producer", "reviewer", "administrator")),
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
    actor: Actor = Depends(require_roles("producer", "reviewer", "administrator")),
    db: Session = Depends(db_session),
):
    return resolve_agent_proposal(project_id, proposal_id, actor, db, "rejected")
