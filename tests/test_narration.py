import hashlib
import sys
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import api, narration, narration_jobs
from app.auth import Actor, get_current_actor
from app.core import (
    Base,
    DispatchOutbox,
    Job,
    MediaAsset,
    Narration,
    NarrationRevision,
    Project,
    ProjectMembership,
    User,
    WorkflowArtifact,
)


class CancellationRequestedError(Exception):
    pass


@pytest.fixture
def context(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)

    def database():
        with sessions() as db:
            yield db

    api.app.dependency_overrides[api.db_session] = database
    api.app.dependency_overrides[get_current_actor] = lambda: Actor(
        name="editor", user_id="editor", roles=["viewer"])
    with sessions() as db:
        db.add_all([User(id="editor", username="editor", password_hash="unused"),
                    User(id="viewer", username="viewer", password_hash="unused"),
                    Project(id="project", owner="editor", name="Synthetic")])
        db.flush()
        db.add_all([ProjectMembership(project_id="project", user_id="editor", role="editor"),
                    ProjectMembership(project_id="project", user_id="viewer", role="viewer")])
        db.add(MediaAsset(id="source", project_id="project", filename="synthetic.mp4",
                          media_type="video/mp4", size_bytes=12, sha256="a" * 64,
                          storage_key="uploads/synthetic-source.mp4"))
        db.commit()
    dispatches = []
    monkeypatch.setattr(api, "dispatch_outbox", lambda job_id: dispatches.append(job_id))
    with TestClient(api.app) as client:
        yield client, sessions, dispatches
    api.app.dependency_overrides.clear()
    engine.dispose()


def start(client):
    result = client.post("/v1/projects/project/narrations",
                         headers={"Idempotency-Key": "synthetic-request"},
                         json={"media_id": "source", "options": {"analysis_mode": "manual"}})
    assert result.status_code == 202, result.text
    return result.json()["id"]


def seed_draft(sessions, narration_id):
    script = [{"id": "manual-1", "start_ms": 0, "end_ms": 90000,
               "text": "Synthetic blue screen.", "evidence": ["manual"]}]
    with sessions() as db:
        row = db.get(Narration, narration_id)
        row.revision = 1
        row.stage = "awaiting_script_approval"
        row.analysis = {"media": {"format": {"duration": "90"}}, "scenes": [],
                        "transcript": {"segments": []}}
        db.get(Job, row.job_id).status = "awaiting_review"
        db.add(NarrationRevision(id=str(uuid.uuid4()), narration_id=row.id, revision=1,
                                 editor_id="editor", script=script))
        db.commit()
    return script


def test_submission_is_durable_idempotent_source_scoped_and_never_wan(context):
    client, sessions, dispatches = context
    narration_id = start(client)
    assert start(client) == narration_id
    with sessions() as db:
        row = db.get(Narration, narration_id)
        job = db.get(Job, row.job_id)
        assert job.operation == "narrate_existing_video"
        assert job.media_id == "source"
        assert job.owner_principal_id == "editor"
        assert db.get(DispatchOutbox, job.id).status == "pending"
        assert db.query(Job).count() == 1
    assert len(dispatches) == 1
    missing = client.post("/v1/projects/project/narrations", headers={"Idempotency-Key": "other"},
                          json={"media_id": "other-project-source"})
    assert missing.status_code == 404


def test_approval_is_revision_bound_and_edits_invalidate_it(context):
    client, sessions, _ = context
    narration_id = start(client)
    script = seed_draft(sessions, narration_id)
    base = f"/v1/projects/project/narrations/{narration_id}"
    assert client.post(f"{base}/render", json={"expected_revision": 1}).status_code == 409
    approved = client.post(f"{base}/approval",
                           json={"expected_revision": 1, "decision": "approved"})
    assert approved.status_code == 200
    assert approved.json()["approved_revision"] == 1
    script[0]["text"] = "Corrected synthetic description."
    edited = client.put(f"{base}/script", json={"expected_revision": 1, "segments": script})
    assert edited.status_code == 200
    assert edited.json()["revision"] == 2
    assert edited.json()["approved_revision"] is None
    assert client.post(f"{base}/render", json={"expected_revision": 1}).status_code == 409
    assert client.put(f"{base}/script", json={"expected_revision": 1, "segments": script}).status_code == 409
    with sessions() as db:
        first = db.query(NarrationRevision).filter_by(narration_id=narration_id, revision=1).one()
        assert first.approved_by == "editor"
        assert first.script[0]["text"] == "Synthetic blue screen."


def test_viewer_cannot_edit_approve_or_render_and_foreign_reader_is_denied(context):
    client, sessions, _ = context
    narration_id = start(client)
    script = seed_draft(sessions, narration_id)
    api.app.dependency_overrides[get_current_actor] = lambda: Actor(
        name="viewer", user_id="viewer", roles=["viewer"])
    base = f"/v1/projects/project/narrations/{narration_id}"
    assert client.put(f"{base}/script", json={"expected_revision": 1, "segments": script}).status_code == 403
    assert client.post(f"{base}/approval", json={"expected_revision": 1, "decision": "approved"}).status_code == 403
    assert client.post(f"{base}/render", json={"expected_revision": 1}).status_code == 403
    api.app.dependency_overrides[get_current_actor] = lambda: Actor(
        name="foreign", user_id="foreign", roles=["viewer"])
    assert client.get(base).status_code == 404


def test_artifact_listing_is_narration_scoped(context):
    client, sessions, _ = context
    narration_id = start(client)
    with sessions() as db:
        row = db.get(Narration, narration_id)
        db.add(WorkflowArtifact(id="ours", job_id=row.job_id, project_id="project",
                                owner="editor", relative_path="ours/video.mp4",
                                filename="video.mp4", media_type="video/mp4",
                                sha256="a" * 64, size_bytes=12))
        db.add(Job(id="wan-job", project_id="project", owner="editor", operation="wan22_t2v",
                   status="awaiting_review", spec={}))
        db.flush()
        db.add(WorkflowArtifact(id="unrelated", job_id="wan-job", project_id="project",
                                owner="editor", relative_path="other/video.mp4", filename="video.mp4",
                                media_type="video/mp4", sha256="b" * 64, size_bytes=12))
        db.commit()
    result = client.get(f"/v1/projects/project/narrations/{narration_id}").json()
    assert [item["id"] for item in result["artifacts"]] == ["ours"]


def test_narration_job_artifact_uses_project_scoped_download(context, tmp_path, monkeypatch):
    client, sessions, _ = context
    narration_id = start(client)
    content = b"synthetic narration video"
    digest = hashlib.sha256(content).hexdigest()
    with sessions() as db:
        row = db.get(Narration, narration_id)
        job = db.get(Job, row.job_id)
        artifact_id = str(uuid.uuid4())
        relative_path = f"{job.id}/{artifact_id}/video.mp4"
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
        job.status = "awaiting_review"
        job.artifact = relative_path
        job.artifact_id = artifact_id
        job.artifact_sha256 = digest
        db.add(WorkflowArtifact(
            id=artifact_id, job_id=job.id, project_id=row.project_id, owner=job.owner,
            relative_path=relative_path, filename="video.mp4", media_type="video/mp4",
            sha256=digest, size_bytes=len(content),
        ))
        db.commit()
        job_id = job.id
    monkeypatch.setattr(api, "ARTIFACT_ROOT", tmp_path)

    job = client.get(f"/v1/jobs/{job_id}").json()
    assert job["artifact"] == f"/v1/projects/project/artifacts/{artifact_id}"
    assert job["project_artifact_id"] == artifact_id
    response = client.get(job["artifact"])
    assert response.status_code == 200
    assert response.content == content
    assert response.headers["X-Content-SHA256"] == digest


@pytest.mark.parametrize("script", [
    [{"id": "a", "start_ms": 0, "end_ms": 90001, "text": "Too long"}],
    [{"id": "a", "start_ms": 0, "end_ms": 1000, "text": "One"},
     {"id": "b", "start_ms": 500, "end_ms": 1500, "text": "Overlap"}],
])
def test_script_timing_is_bounded(script):
    with pytest.raises(narration.NarrationError):
        narration.validate_script(script, 90000)


def test_no_audio_does_not_invent_transcript_or_require_model(tmp_path):
    result = narration.transcribe(tmp_path / "synthetic.mp4", tmp_path,
                                  {"streams": [{"codec_type": "video"}]}, lambda: None)
    assert result["status"] == "no_audio"
    assert result["segments"] == []


def test_missing_asr_model_is_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("NARRATION_ASR_MODEL", str(tmp_path / "missing"))
    with pytest.raises(narration.NarrationError, match="asr_model_unavailable"):
        narration.transcribe(tmp_path / "synthetic.mp4", tmp_path,
                             {"streams": [{"codec_type": "audio"}]}, lambda: None)


def test_cancel_before_subprocess_does_not_start_tool(monkeypatch):
    def cancelled():
        raise RuntimeError("cancelled")
    monkeypatch.setattr(narration.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("Tool started"))
    with pytest.raises(RuntimeError, match="cancelled"):
        narration.run(["ffmpeg"], cancelled)


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_subprocess_output_limit_kills_tool(monkeypatch, stream):
    child_processes = []
    popen = narration.subprocess.Popen

    def tracked_popen(*args, **kwargs):
        process = popen(*args, **kwargs)
        child_processes.append(process)
        return process

    monkeypatch.setattr(narration.subprocess, "Popen", tracked_popen)
    code = (f"import sys,time; sys.{stream}.write('x' * {2 * 1024 * 1024 + 1}); "
            f"sys.{stream}.flush(); time.sleep(30)")
    with pytest.raises(narration.NarrationError, match="media_diagnostics_limit"):
        narration.run([sys.executable, "-c", code], lambda: None, timeout=5)
    assert child_processes[0].poll() is not None


def test_subprocess_timeout_kills_and_reaps_tool(monkeypatch):
    child_processes = []
    popen = narration.subprocess.Popen

    def tracked_popen(*args, **kwargs):
        process = popen(*args, **kwargs)
        child_processes.append(process)
        return process

    monkeypatch.setattr(narration.subprocess, "Popen", tracked_popen)
    with pytest.raises(narration.NarrationError, match="media_timeout"):
        narration.run([sys.executable, "-c", "import time; time.sleep(30)"],
                      lambda: None, timeout=0.1)
    assert child_processes[0].poll() is not None


def test_subprocess_cancellation_kills_and_reaps_tool(monkeypatch):
    child_processes = []
    popen = narration.subprocess.Popen

    def tracked_popen(*args, **kwargs):
        process = popen(*args, **kwargs)
        child_processes.append(process)
        return process

    def check():
        if child_processes:
            raise RuntimeError("cancelled")

    monkeypatch.setattr(narration.subprocess, "Popen", tracked_popen)
    with pytest.raises(RuntimeError, match="cancelled"):
        narration.run([sys.executable, "-c", "import time; time.sleep(30)"], check)
    assert child_processes[0].poll() is not None


def test_timing_overrun_requires_revision_instead_of_truncation(monkeypatch, tmp_path):
    monkeypatch.setattr(narration.shutil, "which", lambda _name: "/usr/bin/espeak-ng")
    monkeypatch.setattr(narration, "run", lambda *_args, **_kwargs: b"")
    monkeypatch.setattr(narration, "probe", lambda *_args: {"format": {"duration": "3"}})
    with pytest.raises(narration.NarrationError, match="timing_revision_required"):
        narration.synthesize(
            [{"id": "too-short", "start_ms": 0, "end_ms": 1000, "text": "A spoken sentence."}],
            tmp_path, narration.NarrationOptions(), 10, lambda: None)


def test_script_can_be_corrected_after_failed_synthesis(context):
    client, sessions, _ = context
    narration_id = start(client)
    script = seed_draft(sessions, narration_id)
    with sessions() as db:
        row = db.get(Narration, narration_id)
        row.stage = "failed"
        row.error = "timing_revision_required"
        db.get(Job, row.job_id).status = "failed"
        db.commit()
    base = f"/v1/projects/project/narrations/{narration_id}"
    edited = client.put(f"{base}/script", json={"expected_revision": 1, "segments": script})
    assert edited.status_code == 200
    assert client.get(base).json()["stage"] == "awaiting_script_approval"
    approved = client.post(f"{base}/approval",
                           json={"expected_revision": 2, "decision": "approved"})
    assert approved.status_code == 200


def test_draft_does_not_invent_source_events():
    analysis = {
        "media": {"format": {"duration": "90"}}, "scenes": [],
        "transcript": {"segments": [{"id": "speech-1", "start_ms": 0, "end_ms": 2000,
                                     "text": "A synthetic instruction."}]},
    }
    script = narration.draft(analysis, narration.NarrationOptions(instructions="Invent a procedure"))
    assert script[0]["text"] == "A synthetic instruction."
    assert script[0]["evidence"] == ["speech-1"]
    assert "hypothesis" in script[0]["uncertainty"]


@pytest.mark.parametrize("mode,speech,failure", [
    ("transcript", True, narration.NarrationError("vision_unavailable")),
    ("transcript", True, httpx.ReadTimeout("local vision timed out")),
    ("manual", False, narration.NarrationError("ocr_service_unavailable")),
    ("transcript", False, narration.NarrationError("vision_unavailable")),
    ("transcript", True, CancellationRequestedError()),
])
def test_optional_visual_failure_preserves_narration_or_cancellation(
    context, monkeypatch, tmp_path, mode, speech, failure,
):
    client, sessions, _ = context
    narration_id = start(client)
    source = tmp_path / "source" / "video.mp4"
    narration_jobs.write_private_file(tmp_path, "source/video.mp4", b"synthetic123")
    source_hash = narration.digest(source)
    monkeypatch.setattr(narration_jobs, "ARTIFACT_ROOT", tmp_path)
    monkeypatch.setattr(narration, "probe", lambda *_args: {
        "format": {"duration": "90"}, "streams": [{"codec_type": "video"}],
    })
    transcript = {
        "status": "transcribed" if speech else "no_audio", "language": "en" if speech else None,
        "segments": [{"id": "speech-1", "start_ms": 0, "end_ms": 2000,
                      "text": "Verified synthetic speech."}] if speech else [],
    }

    def transcribe(*_args):
        assert mode == "transcript"
        return transcript

    def unavailable(*_args):
        raise failure

    monkeypatch.setattr(narration, "transcribe", transcribe)
    monkeypatch.setattr(narration, "scenes", unavailable)
    with sessions() as db:
        row = db.get(Narration, narration_id)
        row.source_sha256 = source_hash
        row.options = {**row.options, "analysis_mode": mode, "vision": True}
        asset = db.get(MediaAsset, row.media_id)
        asset.sha256 = source_hash
        asset.storage_key = "source/video.mp4"
        db.commit()
        job = db.get(Job, row.job_id)
        if isinstance(failure, CancellationRequestedError):
            with pytest.raises(CancellationRequestedError):
                narration_jobs.execute(db, job, lambda: None)
            assert db.query(NarrationRevision).count() == 0
            return
        narration_jobs.execute(db, job, lambda: None)
        assert row.stage == "awaiting_script_approval"
        assert row.error is None
        assert row.analysis["scene_analysis"]["status"] == "unavailable"
        assert row.analysis["scene_analysis"]["error"]
        assert row.analysis["scenes"] == []
        revision = db.query(NarrationRevision).filter_by(narration_id=row.id).one()
        assert revision.script[0]["text"] == ("Verified synthetic speech." if speech else "")
        if speech:
            assert row.analysis["transcript"] == transcript
            assert revision.script[0]["evidence"] == ["speech-1"]


def mock_ocr_client(monkeypatch, handler):
    client_type = httpx.Client
    captured = {}
    transport = httpx.MockTransport(handler)

    def create_client(**kwargs):
        captured.update(kwargs)
        return client_type(transport=transport, **kwargs)

    monkeypatch.setattr(narration.httpx, "Client", create_client)
    return captured


def synthetic_jpeg(width=2, height=2):
    return (
        b"\xff\xd8\xff\xc0\x00\x0b\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x01\x01\x11\x00\xff\xd9"
    )


def test_extract_ocr_uses_bounded_docling_rapidocr_request(monkeypatch, tmp_path):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(synthetic_jpeg())
    requests = []

    def handle(request):
        requests.append(request)
        assert str(request.url) == "http://docling-ocr:9623/v1/convert/file"
        assert b'name="ocr_preset"\r\n\r\nrapidocr' in request.content
        assert b'name="do_ocr"\r\n\r\ntrue' in request.content
        assert b'name="files"; filename="frame.jpg"' in request.content
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"status": "success", "document": {"text_content": "  Save changes  \n"}},
        )

    settings = mock_ocr_client(monkeypatch, handle)
    result = narration.extract_ocr(frame, lambda: None)

    assert len(requests) == 1
    assert settings["trust_env"] is False
    assert settings["follow_redirects"] is False
    assert result["text"] == "Save changes"
    assert result["provenance"]["provider_version"] == "1.38.2"
    assert result["provenance"]["engine"] == "rapidocr"
    assert result["provenance"]["engine_version"] == "3.10.0"
    assert result["provenance"]["model_sha256"] == narration.OCR_MODEL_SHA256


@pytest.mark.parametrize(("content", "error"), [
    (b"not a jpeg", "ocr_frame_invalid"),
    (b"\xff\xd8\xff" + b"x" * (narration.OCR_MAX_IMAGE_BYTES + 1) + b"\xff\xd9",
     "ocr_frame_invalid"),
    (synthetic_jpeg(width=961), "ocr_frame_dimensions_invalid"),
    (synthetic_jpeg(height=541), "ocr_frame_dimensions_invalid"),
])
def test_extract_ocr_rejects_invalid_oversized_or_large_frames(
    monkeypatch, tmp_path, content, error,
):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(content)
    mock_ocr_client(
        monkeypatch,
        lambda _request: pytest.fail("Invalid frame must not be submitted"),
    )

    with pytest.raises(narration.NarrationError, match=error):
        narration.extract_ocr(frame, lambda: None)


def test_extract_ocr_bounds_service_response(monkeypatch, tmp_path):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(synthetic_jpeg())
    mock_ocr_client(monkeypatch, lambda _request: httpx.Response(
        200, headers={"content-type": "application/json"},
        content=b"x" * (narration.OCR_MAX_RESPONSE_BYTES + 1),
    ))

    with pytest.raises(narration.NarrationError, match="ocr_response_limit"):
        narration.extract_ocr(frame, lambda: None)


@pytest.mark.parametrize(("document", "error"), [
    ({}, "ocr_service_invalid_response"),
    ({"text_content": None}, "ocr_service_invalid_response"),
    ({"text_content": 123}, "ocr_service_invalid_response"),
])
def test_extract_ocr_rejects_missing_or_invalid_text(monkeypatch, tmp_path, document, error):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(synthetic_jpeg())
    mock_ocr_client(monkeypatch, lambda _request: httpx.Response(
        200, json={"status": "success", "document": document},
    ))
    with pytest.raises(narration.NarrationError, match=error):
        narration.extract_ocr(frame, lambda: None)


def test_extract_ocr_reports_timeout_without_retry(monkeypatch, tmp_path):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(synthetic_jpeg())
    attempts = []

    def timeout(request):
        attempts.append(request)
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    mock_ocr_client(monkeypatch, timeout)
    with pytest.raises(narration.NarrationError, match="ocr_service_timeout"):
        narration.extract_ocr(frame, lambda: None)
    assert len(attempts) == 1


@pytest.mark.parametrize(("ocr_enabled", "vision_enabled"), [
    (True, False),
    (False, True),
    (True, True),
    (False, False),
])
def test_scenes_runs_ocr_and_vision_independently(
    monkeypatch, tmp_path, ocr_enabled, vision_enabled,
):
    def fake_run(args, _check, _timeout=120, env=None):
        if str(args[-1]).endswith(".jpg"):
            Path(args[-1]).write_bytes(synthetic_jpeg())
            return b""
        return bytes(64 * 36)

    ocr_calls = []
    vision_calls = []
    monkeypatch.setattr(narration, "run", fake_run)
    monkeypatch.setattr(narration, "extract_ocr", lambda *_args: (
        ocr_calls.append(True) or {
            "text": "Save changes", "provenance": {"engine": "rapidocr"},
        }
    ))
    monkeypatch.setattr(narration, "vision", lambda *_args: (
        vision_calls.append(True) or {"text": "A visible button", "uncertain": True}
    ))
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()

    result = narration.scenes(
        tmp_path / "source.mp4", frames_dir, {"format": {"duration": "1"}},
        narration.NarrationOptions(
            analysis_mode="manual", ocr=ocr_enabled, vision=vision_enabled,
        ), lambda: None,
    )

    assert len(ocr_calls) == int(ocr_enabled)
    assert len(vision_calls) == int(vision_enabled)
    assert result[0]["timestamp_ms"] == 0
    assert result[0]["id"] == "frame-0"
    assert result[0]["ocr"] == ("Save changes" if ocr_enabled else None)
    assert result[0]["ocr_status"] == ("complete" if ocr_enabled else "not_requested")
    assert result[0]["ocr_provenance"] == (
        {"engine": "rapidocr"} if ocr_enabled else None
    )
    assert result[0]["vision"] == (
        {"text": "A visible button", "uncertain": True} if vision_enabled else None
    )
    assert result[0]["vision_status"] == (
        "complete" if vision_enabled else "not_requested"
    )


def test_scenes_records_ocr_failure_and_still_runs_vision(monkeypatch, tmp_path):
    def fake_run(args, _check, _timeout=120, env=None):
        if str(args[-1]).endswith(".jpg"):
            Path(args[-1]).write_bytes(synthetic_jpeg())
            return b""
        return bytes(64 * 36)

    monkeypatch.setattr(narration, "run", fake_run)
    monkeypatch.setattr(narration, "extract_ocr", lambda *_args: (
        _ for _ in ()
    ).throw(narration.NarrationError("ocr_service_timeout")))
    monkeypatch.setattr(narration, "vision", lambda *_args: {
        "text": "A visible button", "uncertain": True,
    })
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()

    result = narration.scenes(
        tmp_path / "source.mp4", frames_dir, {"format": {"duration": "1"}},
        narration.NarrationOptions(analysis_mode="manual", vision=True), lambda: None,
    )

    assert result[0]["ocr"] is None
    assert result[0]["ocr_status"] == "unavailable"
    assert result[0]["ocr_error"] == "ocr_service_timeout"
    assert result[0]["vision"]["text"] == "A visible button"
    assert result[0]["vision_status"] == "complete"


@pytest.mark.parametrize(("ocr_text", "ocr_status"), [
    ("", "no_text"),
    ("Synthetic title", "complete"),
])
def test_scenes_keeps_ocr_when_vision_fails(monkeypatch, tmp_path, ocr_text, ocr_status):
    def fake_run(args, _check, _timeout=120, env=None):
        if str(args[-1]).endswith(".jpg"):
            Path(args[-1]).write_bytes(synthetic_jpeg())
            return b""
        return bytes(64 * 36)

    monkeypatch.setattr(narration, "run", fake_run)
    monkeypatch.setattr(narration, "extract_ocr", lambda *_args: {
        "text": ocr_text, "provenance": {"engine": "rapidocr"},
    })
    monkeypatch.setattr(narration, "vision", lambda *_args: (
        _ for _ in ()
    ).throw(httpx.ReadTimeout("sensitive provider detail")))
    result = narration.scenes(
        tmp_path / "source.mp4", tmp_path, {"format": {"duration": "1"}},
        narration.NarrationOptions(analysis_mode="manual", vision=True), lambda: None,
    )
    assert result[0]["ocr"] == ocr_text
    assert result[0]["ocr_status"] == ocr_status
    assert result[0]["vision"] is None
    assert result[0]["vision_status"] == "unavailable"
    assert result[0]["vision_error"] == "vision_provider_request_or_response_failed"


def test_scenes_does_not_swallow_cancellation_after_provider_failure(monkeypatch, tmp_path):
    def fake_run(args, _check, _timeout=120, env=None):
        if str(args[-1]).endswith(".jpg"):
            Path(args[-1]).write_bytes(synthetic_jpeg())
            return b""
        return bytes(64 * 36)

    checks = 0

    def check():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise CancellationRequestedError()

    monkeypatch.setattr(narration, "run", fake_run)
    monkeypatch.setattr(narration, "extract_ocr", lambda *_args: (
        _ for _ in ()
    ).throw(narration.NarrationError("ocr_service_timeout")))
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()

    with pytest.raises(CancellationRequestedError):
        narration.scenes(
            tmp_path / "source.mp4", frames_dir, {"format": {"duration": "1"}},
            narration.NarrationOptions(analysis_mode="manual"), check,
        )
