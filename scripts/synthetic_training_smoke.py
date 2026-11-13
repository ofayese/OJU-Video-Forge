"""Isolated synthetic training smoke with a fake agent and real CPU media tools."""

from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import api, narration as media, narration_jobs
from app.auth import Actor, get_current_actor
from app.config import Settings
from app.core import Base, Job, MediaAsset, Project, ProjectMembership, User


class SyntheticAgent:
    def generate_training(self, evidence, check):
        check()
        assert evidence["approved_steps"][0]["evidence"] == ["frame-0"]
        claim = {"text": "A blue screen is visible.", "kind": "evidence", "evidence": ["frame-0"]}
        timed = {**claim, "id": "segment-1", "start_ms": 0, "end_ms": 4000}
        return {
            "title": "Synthetic training", "audience": "Beginner", "prerequisites": [],
            "learning_objectives": [claim], "chapters": [timed], "script": [timed],
            "annotations": [timed], "technical_explanations": [], "lab_steps": [],
            "validation_checkpoints": [], "troubleshooting": [], "assessment": [],
            "answer_explanations": [],
        }

    def provenance(self):
        return {"provider": "synthetic", "model": "test", "model_digest": "a" * 64}


with TemporaryDirectory() as tmp:
    root = Path(tmp)
    source = root / "uploads" / "source.mp4"
    source.parent.mkdir()
    media.run([
        "ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
        "color=c=blue:s=320x240:r=10", "-t", "4", "-c:v", "mpeg4",
        "-threads", "1", str(source),
    ], lambda: None, 30)
    original = media.digest(source)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)

    def database():
        with sessions() as db:
            yield db

    api.app.dependency_overrides[api.db_session] = database
    api.app.dependency_overrides[get_current_actor] = lambda: Actor(
        name="editor", user_id="editor", roles=["viewer"]
    )
    api.ARTIFACT_ROOT = root
    narration_jobs.ARTIFACT_ROOT = root
    api.dispatch_outbox = lambda _id: None
    api.get_settings = lambda: Settings(agents_enabled=True)
    api.get_agent_adapter = lambda: SyntheticAgent()
    narration_jobs.get_agent_adapter = lambda: SyntheticAgent()
    with sessions() as db:
        db.add(User(id="editor", username="editor", password_hash="unused"))
        db.add(Project(id="project", owner="editor", name="Synthetic"))
        db.flush()
        db.add(ProjectMembership(project_id="project", user_id="editor", role="editor"))
        db.add(MediaAsset(
            id="source", project_id="project", filename="source.mp4", media_type="video/mp4",
            size_bytes=source.stat().st_size, sha256=original, storage_key="uploads/source.mp4",
        ))
        db.commit()
    with TestClient(api.app) as client:
        def ok(response, expected):
            assert response.status_code == expected, (response.status_code, response.text)
            return response.json()

        row = ok(client.post(
            "/v1/projects/project/narrations", headers={"Idempotency-Key": "synthetic"},
            json={"media_id": "source", "options": {
                "workflow": "silent_training", "ocr": False, "vision": False,
            }},
        ), 202)
        base = "/v1/projects/project/narrations/" + row["id"]
        with sessions() as db:
            narration_jobs.execute(db, db.get(Job, row["job_id"]), lambda: None)
        row = ok(client.get(base), 200)
        assert row["stage"] == "awaiting_procedure_review" and not row["analysis"]["transcript"]["segments"]
        frame_id = row["analysis"]["scenes"][0]["artifact_id"]
        assert client.get("/v1/projects/project/artifacts/" + frame_id).status_code == 200
        api.app.dependency_overrides[get_current_actor] = lambda: Actor(
            name="outsider", user_id="outsider", roles=["viewer"]
        )
        assert client.get("/v1/projects/project/artifacts/" + frame_id).status_code == 404
        api.app.dependency_overrides[get_current_actor] = lambda: Actor(
            name="editor", user_id="editor", roles=["viewer"]
        )
        steps = row["analysis"]["procedure"]["steps"]
        steps[0]["decision"] = "accepted"
        steps[0]["description"] = "A blue screen is visible."
        steps[0]["confidence"] = "high"
        steps[0]["evidence_category"] = "direct_observation"
        row = ok(client.post(
            base + "/procedure-review", json={"expected_revision": row["revision"], "steps": steps},
        ), 200)
        row = ok(client.post(base + "/training-draft", json={"expected_revision": row["revision"]}), 202)
        with sessions() as db:
            narration_jobs.execute(db, db.get(Job, row["job_id"]), lambda: None)
        row = ok(client.get(base), 200)
        assert row["stage"] == "awaiting_script_approval"
        row = ok(client.post(base + "/approval", json={
            "expected_revision": row["revision"], "decision": "approved",
        }), 200)
        row = ok(client.post(base + "/render", json={
            "expected_revision": row["revision"], "render_annotations": True,
        }), 202)
        with sessions() as db:
            narration_jobs.execute(db, db.get(Job, row["job_id"]), lambda: None)
        row = ok(client.get(base), 200)
        artifacts = {artifact["filename"]: artifact for artifact in row["artifacts"]}
        assert {"video.mp4", "narration.wav", "evidence-manifest.json",
                "training-module.json", "training-module.md", "chapter-index.json",
                "lab-guide.md", "learner-handout.md", "assessment.md",
                "answer-key.md"} <= artifacts.keys()
        video = client.get("/v1/projects/project/artifacts/" + artifacts["video.mp4"]["id"])
        assert video.status_code == 200 and len(video.content) > 0
        derived = root / "derived.mp4"
        derived.write_bytes(video.content)
        assert media.video_hash(source, lambda: None) != media.video_hash(derived, lambda: None)
        def pixels(path):
            return media.run([
                "ffmpeg", "-nostdin", "-v", "error", "-ss", "1", "-i", str(path),
                "-vf", "crop=240:40:0:0", "-frames:v", "1", "-pix_fmt", "rgb24",
                "-threads", "1", "-f", "rawvideo", "-",
            ], lambda: None, 30)
        before, after = pixels(source), pixels(derived)
        assert len(before) == len(after) == 240 * 40 * 3
        assert sum(abs(a - b) for a, b in zip(before, after)) / len(before) > 10
        assert media.digest(source) == original
        manifest = ok(client.get(
            "/v1/projects/project/artifacts/" + artifacts["evidence-manifest.json"]["id"],
        ), 200)
        assert manifest["source_sha256"] == original
        print("PASS: synthetic API -> sampled evidence -> review -> mocked agent -> approval -> real eSpeak/FFmpeg")
        print("Source SHA-256:", original, "video SHA-256:", artifacts["video.mp4"]["sha256"])
    api.app.dependency_overrides.clear()
    engine.dispose()
