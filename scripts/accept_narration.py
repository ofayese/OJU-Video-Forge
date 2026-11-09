"""One real, synthetic 90-second manual-script narration acceptance.

Run only in a disposable narration container with an isolated SQLite database,
private /tmp storage and a disposable Redis instance. No ASR model is downloaded.
"""

import hashlib
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from fastapi.testclient import TestClient
from redis import Redis
from rq import SimpleWorker

from app.api import app
from app.core import Base, engine, queue
from app.narration import probe, run, video_hash


def main():
    if (not os.environ.get("DATABASE_URL", "").startswith("sqlite:////tmp/")
            or not os.environ.get("ARTIFACT_ROOT", "").startswith("/tmp/")
            or urlparse(os.environ.get("REDIS_URL", "")).hostname != "narration-acceptance-redis"):
        raise RuntimeError("Disposable SQLite, storage and Redis are required")
    Base.metadata.create_all(engine)

    def work():
        SimpleWorker([queue()], connection=Redis.from_url(os.environ["REDIS_URL"])).work(
            burst=True, logging_level="WARNING")

    with tempfile.TemporaryDirectory() as temporary, TestClient(app) as client:
        directory = Path(temporary)
        speech = directory / "source.txt"
        speech.write_text("This synthetic demonstration shows a blue panel, then a green panel, then a red panel.")
        wav = directory / "source.wav"
        run(["espeak-ng", "-v", "en-us", "-s", "160", "-f", str(speech), "-w", str(wav)], lambda: None)
        source = directory / "source.mp4"
        run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=640x360:r=12",
             "-i", str(wav), "-vf",
             "drawbox=x=0:y=0:w=iw:h=ih:color=green:t=fill:enable='between(t,30,60)',"
             "drawbox=x=0:y=0:w=iw:h=ih:color=red:t=fill:enable='gte(t,60)',"
             "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:"
             "text='SYNTHETIC TUTORIAL':fontcolor=white:fontsize=32:x=40:y=40",
             "-af", "apad", "-t", "90", "-c:v", "libx264", "-preset", "ultrafast",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-threads", "2", str(source)], lambda: None)
        original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        credentials = {"username": "synthetic-narration", "password": os.environ.get("NARRATION_ACCEPT_PASSWORD","Synthetic-acceptance-only-2749!")}
        assert client.post("/v1/auth/register", json=credentials).status_code == 201
        login = client.post("/v1/auth/session", json=credentials)
        assert login.status_code == 200, login.text
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        project = client.post("/v1/projects", json={"name": "Synthetic narration acceptance"})
        assert project.status_code == 201, project.text
        project_id = project.json()["id"]
        upload = client.post(f"/v1/projects/{project_id}/media",
                             files={"file": ("synthetic.mp4", source.read_bytes(), "video/mp4")})
        assert upload.status_code == 201, upload.text
        media_id = upload.json()["id"]
        base = f"/v1/projects/{project_id}/narrations"
        started = client.post(base, headers={"Idempotency-Key": "synthetic-90-second-analysis"},
                              json={"media_id": media_id, "options": {"analysis_mode": "manual"}})
        assert started.status_code == 202, started.text
        narration_id = started.json()["id"]
        endpoint = f"{base}/{narration_id}"
        work()
        draft = client.get(endpoint).json()
        assert draft["stage"] == "awaiting_script_approval", draft
        assert len(draft["analysis"]["scenes"]) == 9
        assert any("SYNTHETIC" in (frame["ocr"] or "") for frame in draft["analysis"]["scenes"])
        assert any(frame["major_change"] for frame in draft["analysis"]["scenes"])
        assert draft["analysis"]["transcript"]["status"] == "manual_not_transcribed"
        segments = [
            {"id": "blue", "start_ms": 0, "end_ms": 10000,
             "text": "This synthetic tutorial begins with a blue panel.", "evidence": ["frame-0", "manual"]},
            {"id": "green", "start_ms": 30000, "end_ms": 40000,
             "text": "The synthetic panel is now green.", "evidence": ["frame-3", "manual"]},
            {"id": "red", "start_ms": 60000, "end_ms": 70000,
             "text": "The synthetic panel is now red.", "evidence": ["frame-6", "manual"]},
        ]
        edited = client.put(f"{endpoint}/script",
                            json={"expected_revision": draft["revision"], "segments": segments})
        assert edited.status_code == 200, edited.text
        revision = edited.json()["revision"]
        assert client.post(f"{endpoint}/render", json={"expected_revision": revision}).status_code == 409
        approval = client.post(f"{endpoint}/approval",
                               json={"expected_revision": revision, "decision": "approved"})
        assert approval.status_code == 200, approval.text
        preview = client.post(f"{endpoint}/voice-preview", json={"expected_revision": revision})
        assert preview.status_code == 202, preview.text
        original_path = os.environ["PATH"]
        try:
            os.environ["PATH"] = "/missing-acceptance-tools"
            work()
        finally:
            os.environ["PATH"] = original_path
        assert client.get(endpoint).json()["stage"] == "failed"
        retried = client.post(f"{endpoint}/retry")
        assert retried.status_code == 202, retried.text
        work()
        recovered = client.get(endpoint).json()
        assert recovered["stage"] == "script_approved", recovered
        preview_audio = next(item for item in recovered["artifacts"]
                             if item["job_id"] == recovered["job_id"]
                             and item["filename"] == "voice-preview.wav")
        preview_download = client.get(f"/v1/projects/{project_id}/artifacts/{preview_audio['id']}")
        assert preview_download.status_code == 200
        assert preview_download.content[:4] == b"RIFF"
        assert len(preview_download.content) > 10000
        render = client.post(f"{endpoint}/render",
                             json={"expected_revision": revision, "audio_mode": "mix",
                                   "voice": "en-us", "rate": 160})
        assert render.status_code == 202, render.text
        work()
        finished = client.get(endpoint).json()
        assert finished["stage"] == "awaiting_review", finished
        artifacts = {item["filename"]: item for item in finished["artifacts"]
                     if item["job_id"] == finished["job_id"]}
        video = artifacts["video.mp4"]
        retrieved = client.get(f"/v1/projects/{project_id}/artifacts/{video['id']}")
        assert retrieved.status_code == 200
        assert hashlib.sha256(retrieved.content).hexdigest() == video["sha256"]
        output = directory / "derived.mp4"
        output.write_bytes(retrieved.content)
        assert video_hash(source, lambda: None) == video_hash(output, lambda: None)
        assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
        assert "narration.wav" in artifacts
        print(json.dumps({
            "acceptance": "PASS_manual_script_local_tts_and_preserved_video",
            "asr": "NOT_VERIFIED_model_download_not_authorized",
            "vision": "OCR_verified_VLM_not_requested",
            "source": probe(source, lambda: None),
            "output": probe(output, lambda: None),
            "approved_revision": revision, "segments": len(segments),
            "frames": len(draft["analysis"]["scenes"]), "tts": "espeak-ng/en-us",
            "voice_preview_failure_retry": "PASS_restored_approval_and_real_audio",
            "output_bytes": len(retrieved.content), "sha256": video["sha256"],
            "visual_stream_identity": "PASS", "authorized_retrieval": "PASS",
        }, sort_keys=True))


if __name__ == "__main__":
    main()
