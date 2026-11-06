import json
import shutil
import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import rendering, worker
from app.core import Audit, Base, Job


@pytest.fixture
def staged_job(tmp_path, monkeypatch):
    database = create_engine("sqlite://")
    Base.metadata.create_all(database)
    sessions = sessionmaker(database, expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    monkeypatch.setattr(worker, "ARTIFACT_ROOT", tmp_path)
    job_id = str(uuid.uuid4())
    inputs = tmp_path / job_id / "inputs"
    inputs.mkdir(parents=True)
    timeline = {"version": 1, "clips": [{"path": "clip.mp4", "in_ms": 0, "out_ms": 1000}],
                "output": {"profile": "preview", "format": "mp4"}}
    with sessions() as db:
        db.add(Job(id=job_id, owner="owner", operation="render_timeline_v1",
                   status="queued", spec={"timeline": timeline}))
        db.commit()
    yield sessions, job_id, inputs
    database.dispose()


def test_invalid_timeline_records_failure(staged_job, monkeypatch):
    sessions, job_id, _ = staged_job
    with sessions() as db:
        job = db.get(Job, job_id)
        job.spec = {"timeline": {"version": 2}}
        db.commit()
    monkeypatch.setattr(rendering.RenderEngine, "_tool", staticmethod(lambda value: value))
    with pytest.raises(rendering.RenderError, match="required"):
        worker.execute(job_id)
    with sessions() as db:
        job = db.get(Job, job_id)
        assert job.status == "failed"
        assert job.error == "RenderError: timeline render failed"
        assert job.artifact is None
        assert list(db.scalars(select(Audit.action).order_by(Audit.id))) == ["running", "failed"]
    worker.execute(job_id)
    with sessions() as db:
        assert len(list(db.scalars(select(Audit)))) == 2


def test_worker_failure_records_failure(staged_job, monkeypatch):
    sessions, job_id, _ = staged_job
    monkeypatch.setattr(rendering.RenderEngine, "_tool", staticmethod(lambda value: value))

    def fail(*args):
        raise RuntimeError("private detail")

    monkeypatch.setattr(rendering.RenderEngine, "render", fail)
    with pytest.raises(RuntimeError):
        worker.execute(job_id)
    with sessions() as db:
        job = db.get(Job, job_id)
        assert job.status == "failed"
        assert job.error == "RuntimeError: timeline render failed"
        assert list(db.scalars(select(Audit.action).order_by(Audit.id))) == ["running", "failed"]


def test_symlinked_staging_is_rejected(staged_job, tmp_path):
    sessions, job_id, inputs = staged_job
    inputs.rmdir()
    inputs.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="staged"):
        worker.execute(job_id)
    with sessions() as db:
        assert db.get(Job, job_id).status == "failed"


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                    reason="Local FFmpeg and FFprobe are not available")
def test_real_worker_render_records_provenance(staged_job):
    sessions, job_id, inputs = staged_job
    rendering._run([shutil.which("ffmpeg"), "-nostdin", "-n", "-v", "error",
                    "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24",
                    "-t", "1", "-an", "-c:v", "libx264", "-threads", "1",
                    "-pix_fmt", "yuv420p", "clip.mp4"], inputs, 30)
    worker.execute(job_id)
    with sessions() as db:
        job = db.get(Job, job_id)
        assert job.status == "awaiting_review"
        artifact = worker.ARTIFACT_ROOT / job.artifact
        provenance = json.loads((inputs.parent / "provenance.json").read_text())
        assert artifact.is_file()
        assert provenance["output_sha256"] == worker.sha256(artifact)
        assert provenance["inputs_sha256"]["clip.mp4"] == worker.sha256(inputs / "clip.mp4")
        assert provenance["tools"]["ffmpeg"]["version"].startswith("ffmpeg version ")
        assert list(db.scalars(select(Audit.action).order_by(Audit.id))) == ["running", "awaiting_review"]
    worker.execute(job_id)
    with sessions() as db:
        assert len(list(db.scalars(select(Audit)))) == 2
