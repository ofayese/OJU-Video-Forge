import asyncio
import uuid

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from psycopg import AsyncConnection
from redis import Redis
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from .auth import Actor, require_roles
from .core import Audit, DATABASE_URL, Job, REDIS_URL, SessionLocal, queue

app = FastAPI(title="OJU Video Forge", version="0.1.0", docs_url=None, redoc_url=None, openapi_url="/openapi.json")

DATABASE_PROBE_TIMEOUT_SECONDS = 1.5


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


def db_session():
    with SessionLocal() as db:
        yield db


@app.get("/health/live", include_in_schema=False)
def live():
    return {"ok": True}


DATABASE_CLOSE_TIMEOUT_SECONDS = 0.1


async def _database_probe():
    connection = None
    try:
        connection = await AsyncConnection.connect(
            make_url(DATABASE_URL)
            .set(drivername="postgresql")
            .render_as_string(hide_password=False),
            connect_timeout=1,
            options="-c statement_timeout=1000",
        )
        await connection.execute("SELECT 1")
        return True
    except Exception:
        return False
    finally:
        if connection is not None:
            try:
                await asyncio.wait_for(
                    connection.close(), timeout=DATABASE_CLOSE_TIMEOUT_SECONDS
                )
            except Exception:
                pass


async def _database_ready():
    try:
        return await asyncio.wait_for(
            _database_probe(), timeout=DATABASE_PROBE_TIMEOUT_SECONDS
        )
    except Exception:
        return False


@app.get("/health/ready", include_in_schema=False)
async def ready():
    dependencies = {"database": False, "redis": False}
    dependencies["database"] = await _database_ready()
    redis_client = None
    try:
        redis_client = Redis.from_url(
            REDIS_URL, socket_connect_timeout=1, socket_timeout=1
        )
        redis_client.ping()
        dependencies["redis"] = True
    except Exception:
        pass
    finally:
        if redis_client is not None:
            try:
                redis_client.close()
            except Exception:
                pass

    is_ready = all(dependencies.values())
    return JSONResponse(
        status_code=200 if is_ready else 503,
        content={
            "ready": is_ready,
            "required": dependencies,
            "optional": {"gpu": "not_required", "cloud": "not_required"},
        },
    )


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
    if not job or job.owner != actor.name:
        raise HTTPException(404, "Job not found")
    return JobResult(id=job.id, operation=job.operation, status=job.status, artifact=job.artifact, error=job.error)


@app.get("/v1/models", operation_id="listVideoModels")
def get_models(
    actor: Actor = Depends(require_roles("viewer", "producer", "reviewer", "publisher", "auditor", "administrator", "agent_service"))
):
    return {"models": [{"id": "wan22-ti2v-5b", "operation": "wan22_t2v", "status": "requires_workflow_registration"}]}
