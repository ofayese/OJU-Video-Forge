"""Real API/PostgreSQL/Redis/RQ/FFmpeg acceptance using synthetic private fixtures.

Run only in an isolated Compose project. Output contains synthetic IDs and bounded
metadata, never passwords, tokens, filesystem keys, or media bytes.
"""

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.core import (
    Audit,
    Job,
    ProjectMembership,
    SecurityEvent,
    SessionLocal,
    WorkflowArtifact,
)


def checked(response, code):
    if response.status_code != code:
        raise RuntimeError(f"Unexpected HTTP status: {response.status_code}, expected {code}")
    return response.json() if response.content else None


def run(base_url, outage, interrupt):
    if (get_settings().app_env != "test"
            or os.environ.get("FORGE_TEST_PROJECT") not in {
                "oju-video-forge-acceptance", "oju-video-forge-integration",
            }):
        raise RuntimeError("Synthetic acceptance writes require an explicit isolated test environment")
    suffix = uuid.uuid4().hex[:12]
    username = f"synthetic-{suffix}"
    password = secrets.token_urlsafe(24)
    account = {"username": username, "password": password}
    with httpx.Client(base_url=base_url, timeout=15) as client, tempfile.TemporaryDirectory() as temp:
        checked(client.post("/v1/auth/register", json=account), 201)
        session = checked(client.post("/v1/auth/session", json=account), 200)
        client.headers["Authorization"] = f"Bearer {session['access_token']}"
        project = checked(client.post("/v1/projects", json={"name": "Synthetic Docker acceptance"}), 201)
        project_id = project["id"]
        assert project["role"] == "owner"
        with SessionLocal() as db:
            assert db.scalar(select(ProjectMembership).where(
                ProjectMembership.project_id == project_id,
                ProjectMembership.user_id == session["user"]["id"],
                ProjectMembership.role == "owner",
            ))
        other = {"username": f"other-{suffix}", "password": secrets.token_urlsafe(24)}
        checked(client.post("/v1/auth/register", json=other), 201)
        other_session = checked(client.post("/v1/auth/session", json=other), 200)
        other_headers = {"Authorization": f"Bearer {other_session['access_token']}"}
        assert client.get(f"/v1/projects/{project_id}", headers=other_headers).status_code == 404
        assert client.get("/v1/projects", headers={"Authorization": ""}).status_code == 401
        assert client.post(f"/v1/projects/{project_id}/media",
                           files={"file": ("invalid.mp4", b"not video", "video/mp4")}).status_code == 415
        root = Path(temp)
        media = []
        for index, color in enumerate(("red", "blue")):
            path = root / f"clip{index}.mp4"
            fixture = "testsrc2=size=1280x720:rate=24" if interrupt else f"color=c={color}:size=320x180:rate=24"
            subprocess.run([
                "ffmpeg", "-nostdin", "-n", "-v", "error", "-f", "lavfi", "-i",
                fixture, "-t", "8" if interrupt else "1",
                "-an", "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", str(path),
            ], check=True, timeout=30)
            with path.open("rb") as source:
                media.append(checked(client.post(f"/v1/projects/{project_id}/media",
                    files={"file": (path.name, source, "video/mp4")}), 201))
        audio = root / "tone.wav"
        subprocess.run(["ffmpeg", "-nostdin", "-n", "-v", "error", "-f", "lavfi",
                        "-i", "sine=frequency=440:sample_rate=48000", "-t", "2", str(audio)],
                       check=True, timeout=30)
        with audio.open("rb") as source:
            audio_id = checked(client.post(f"/v1/projects/{project_id}/media",
                files={"file": (audio.name, source, "audio/wav")}), 201)["id"]
        timeline = {
            "version": 1,
            "clips": [{"media_id": item["id"], "in_ms": 0, "out_ms": 1000} for item in media],
            "output": {"profile": "preview", "format": "mp4"},
            "title": {"text": "Synthetic acceptance", "start_ms": 0, "end_ms": 1000},
            "captions": [{"text": "Verified local rendering", "start_ms": 1000, "end_ms": 2000}],
            "audio": {"media_id": audio_id, "in_ms": 0, "volume_percent": 50},
        }
        payload = {"operation": "render_timeline_v1", "timeline": timeline}
        expected_duration, expected_width, expected_height, expected_fps = 2, 640, 360, 24
        if interrupt:
            timeline["clips"] = [{"media_id": media[0]["id"], "in_ms": 0, "out_ms": 7500}] * 16
            timeline["output"]["profile"] = "hd"
            expected_duration, expected_width, expected_height, expected_fps = 120, 1280, 720, 30
        key = uuid.uuid4().hex
        headers = {"Idempotency-Key": key}
        submitted = checked(client.post(f"/v1/projects/{project_id}/jobs", json=payload, headers=headers), 202)
        job_id = submitted["id"]
        duplicate = checked(client.post(f"/v1/projects/{project_id}/jobs", json=payload, headers=headers), 202)
        assert duplicate["id"] == job_id
        if outage:
            assert submitted["status"] == "dispatch_pending"
            print(json.dumps({"event": "outage_job_persisted", "job_id": job_id}), flush=True)
        if interrupt:
            print(json.dumps({"event": "interrupt_job_submitted", "job_id": job_id}), flush=True)
        interrupted_job_id = None
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            job = checked(client.get(f"/v1/jobs/{job_id}"), 200)
            if job["status"] == "awaiting_review":
                break
            if job["status"] in {"failed", "cancelled"}:
                if interrupt and interrupted_job_id is None and job["status"] == "failed":
                    interrupted_job_id = job_id
                    retry = checked(client.post(f"/v1/jobs/{job_id}/retry"), 202)
                    job_id = retry["id"]
                    continue
                raise RuntimeError(f"Render did not complete: {job['status']} {job['error']}")
            time.sleep(1)
        else:
            raise RuntimeError("Render completion deadline exceeded")
        artifact = client.get(job["artifact"])
        assert artifact.status_code == 200
        digest = hashlib.sha256(artifact.content).hexdigest()
        assert digest == artifact.headers["X-Content-SHA256"]
        assert digest == job["review_artifact"]["sha256"]
        assert client.get(job["artifact"], headers=other_headers).status_code == 404
        assert client.get(f"/v1/jobs/{job_id}", headers=other_headers).status_code == 404
        output = root / "verified.mp4"
        output.write_bytes(artifact.content)
        probe = json.loads(subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration,format_name:stream=codec_type,codec_name,width,height,pix_fmt,r_frame_rate,channels,sample_rate",
            "-of", "json", str(output),
        ], timeout=15))
        assert abs(float(probe["format"]["duration"]) - expected_duration) <= 1 / expected_fps
        video = next(s for s in probe["streams"] if s["codec_type"] == "video")
        sound = next(s for s in probe["streams"] if s["codec_type"] == "audio")
        assert (video["codec_name"], video["width"], video["height"], video["pix_fmt"],
                video["r_frame_rate"]) == ("h264", expected_width, expected_height, "yuv420p", f"{expected_fps}/1")
        assert (sound["codec_name"], sound["channels"], sound["sample_rate"]) == ("aac", 2, "48000")
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(output),
                        "-f", "null", "-"], check=True, timeout=30)
        with SessionLocal() as db:
            record = db.get(Job, job_id)
            actions = list(db.scalars(select(Audit.action).where(Audit.job_id == job_id).order_by(Audit.id)))
            assert {"running", "awaiting_review", "artifact_downloaded"} <= set(actions)
            assert ("retry_created" if interrupted_job_id else "dispatch_pending") in actions
            registered = db.get(WorkflowArtifact, record.artifact_id)
            assert registered.sha256 == digest and registered.size_bytes == len(artifact.content)
            assert db.scalar(select(SecurityEvent.id).where(
                SecurityEvent.user_id == session["user"]["id"], SecurityEvent.action == "login_succeeded",
            ))
        health = checked(client.get("/health/stack"), 200)
        assert health["workers"] >= 1 and health["disk_capacity_ok"]
        checked(client.delete("/v1/auth/session"), 204)
        assert client.get("/v1/projects").status_code == 401
        return {
            "result": "PASS", "project_id": project_id, "job_id": job_id,
            "artifact_id": registered.id, "size_bytes": len(artifact.content), "sha256": digest,
            "probe": probe, "audit_actions": actions, "outage_reconciliation": outage,
            "interrupted_job_id": interrupted_job_id,
            "ffmpeg": subprocess.check_output(["ffmpeg", "-version"], text=True).splitlines()[0],
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://api:8000")
    parser.add_argument("--outage", action="store_true")
    parser.add_argument("--interrupt", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.base_url, args.outage, args.interrupt), indent=2))
