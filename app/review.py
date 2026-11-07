"""Review contract v1. Only the current worker's generated MP4 is eligible.

No legacy artifact guessing or client-controlled filesystem access.
"""
import hashlib
import os
import stat
import uuid
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import update

from .core import ARTIFACT_ROOT, Audit, Job, Review


class ArtifactIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=40)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decision: Literal["approved", "rejected"]
    expected_version: int = Field(ge=0, le=2**31 - 2)
    expected_artifact: ArtifactIdentity


def generated_checksum(job, *, artifact=None):
    """Read a bounded regular file using no-follow directory descriptors (Linux).

    The bridge intentionally only recognizes render_test/test.mp4. Comfy prompt
    JSON and pre-migration outputs without persisted identity fail closed.
    """
    if (job.operation != "render_test" or
            str(uuid.UUID(job.id)) != job.id or
            (artifact if artifact is not None else job.artifact) != f"{job.id}/test.mp4"):
        raise ValueError("Unsupported generated artifact")
    flags = os.O_RDONLY | os.O_NOFOLLOW
    root = os.open(ARTIFACT_ROOT, flags | os.O_DIRECTORY)
    try:
        directory = os.open(job.id, flags | os.O_DIRECTORY, dir_fd=root)
        try:
            fd = os.open("test.mp4", flags | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(fd, "rb") as output:
                info = os.fstat(output.fileno())
                if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 256 * 1024 * 1024:
                    raise ValueError("Invalid generated artifact")
                digest = hashlib.sha256()
                total = 0
                for chunk in iter(lambda: output.read(1024 * 1024), b""):
                    total += len(chunk)
                    if total > 256 * 1024 * 1024:
                        raise ValueError("Artifact size exceeded")
                    digest.update(chunk)
                if total != info.st_size:
                    raise ValueError("Artifact changed")
                return digest.hexdigest()
        finally:
            os.close(directory)
    finally:
        os.close(root)


def eligible(job):
    return bool(job.status == "awaiting_review" and job.operation == "render_test" and
                job.artifact == f"{job.id}/test.mp4" and
                job.artifact_id and job.artifact_sha256)


def decide(db, job, actor, payload):
    if not eligible(job):
        raise HTTPException(409, "Job is not awaiting review of a generated artifact")
    try:
        checksum = generated_checksum(job)
    except (OSError, ValueError):
        raise HTTPException(409, "Generated artifact unavailable") from None
    if checksum != job.artifact_sha256:
        raise HTTPException(409, "Generated artifact changed")
    # Authoritative compare-and-swap: independent sessions cannot both win.
    try:
        result = db.execute(
            update(Job).where(
                Job.id == job.id, Job.owner == actor.name,
                Job.status == "awaiting_review",
                Job.review_version == payload.expected_version,
                Job.artifact == job.artifact,
                Job.artifact_id == payload.expected_artifact.id,
                Job.artifact_sha256 == payload.expected_artifact.sha256,
                Job.artifact_sha256 == checksum,
            ).values(status=payload.decision, review_version=payload.expected_version + 1),
            execution_options={"synchronize_session": False},
        )
        if result.rowcount != 1:
            raise HTTPException(409, "Stale review version or artifact")
        db.add(Review(
            job_id=job.id, actor=actor.name, decision=payload.decision,
            artifact_id=payload.expected_artifact.id, artifact_sha256=checksum,
            expected_version=payload.expected_version, version=payload.expected_version + 1,
        ))
        db.add(Audit(job_id=job.id, actor=actor.name, action=payload.decision))
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(job)
    return job
