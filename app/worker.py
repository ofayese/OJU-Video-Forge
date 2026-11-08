import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

from redis import Redis
from rq import Queue, Worker
from sqlalchemy import select, update

from .audio_workflows import (
    MAX_AUDIO_BYTES,
    WORKFLOW_VERSION,
    AudioWorkflowError,
    canonical_hash,
    checksum,
    manifest,
    serialize_subtitles,
    validate_audio,
    validate_captions,
)
from .comfyui import ComfyUIAdapter, WorkflowRegistry
from .config import get_settings
from .core import (
    ARTIFACT_ROOT,
    AudioAsset,
    Job,
    Project,
    ProjectMembership,
    MediaAsset,
    User,
    REDIS_URL,
    SessionLocal,
    WorkflowArtifact,
    recover_jobs,
    transition,
)
from .private_storage import private_file, write_private_file
from .review import generated_checksum
from .rendering import RenderEngine

WORKFLOW = Path("/workflows/wan22-t2v-api.json")
GPU_LOCK_NAME = "oju-video-forge:comfyui:generation"
GPU_LOCK_SECONDS = 7200
GPU_WAIT_SECONDS = 3000
GPU_INPUTS = {"positive_prompt", "seed", "width", "height", "frames", "fps"}
MAX_WORKFLOW_BYTES = 16 * 1024 * 1024
logger = logging.getLogger(__name__)



class ModelProviderUnavailable(RuntimeError):
    pass


class JobCancelled(Exception):
    pass


def _cancel_requested(job_id):
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        return job is None or job.status in ('cancel_requested', 'cancelled')


def _run_ffmpeg(job_id, args, timeout):
    process = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + timeout
    try:
        while process.poll() is None:
            if _cancel_requested(job_id):
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise JobCancelled()
            if time.monotonic() >= deadline:
                process.kill()
                process.wait()
                raise subprocess.TimeoutExpired(args, timeout)
            time.sleep(0.25)
        _, stderr = process.communicate()
        if process.returncode:
            raise subprocess.CalledProcessError(
                process.returncode, args, stderr=stderr
            )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_generation_spec(spec):
    if not isinstance(spec, dict):
        raise ValueError("Invalid generation request")
    prompt = spec.get("prompt")
    if (type(prompt) is not str or not prompt.strip() or len(prompt) > 1000
            or any(not char.isprintable() and char not in "\n\t" for char in prompt)):
        raise ValueError("Invalid generation prompt")
    bounds = {
        "width": (256, 1280),
        "height": (144, 720),
        "frames": (17, 81),
        "fps": (8, 24),
        "seed": (0, 2**32 - 1),
    }
    values = {}
    for field, (lower, upper) in bounds.items():
        value = spec.get(field)
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError("Invalid generation settings")
        values[field] = value
    if (values["width"] % 16 or values["height"] % 16
            or (values["frames"] - 1) % 4 or values["fps"] not in {8, 16, 24}):
        raise ValueError("Unsupported generation settings")
    return {
        "positive_prompt": prompt.strip(),
        "seed": values["seed"],
        "width": values["width"],
        "height": values["height"],
        "frames": values["frames"],
        "fps": values["fps"],
    }


def _workflow_registration():
    if WORKFLOW.is_symlink() or not WORKFLOW.is_file():
        raise RuntimeError("Approved ComfyUI workflow is unavailable")
    if WORKFLOW.stat().st_size > MAX_WORKFLOW_BYTES:
        raise RuntimeError("Approved ComfyUI workflow exceeds the size limit")
    try:
        graph = json.loads(WORKFLOW.read_text(encoding="utf-8"))
        raw_mapping = json.loads(os.environ.get("WAN_NODE_MAPPING", "{}"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError("Approved ComfyUI workflow configuration is invalid") from exc
    if not isinstance(raw_mapping, dict) or set(raw_mapping) != GPU_INPUTS:
        raise RuntimeError("WAN_NODE_MAPPING must bind every supported generation input")
    registry = WorkflowRegistry()
    registry.register("wan22_t2v", graph, raw_mapping)
    return registry


def _verify_generated_mp4(output, spec):
    path = output.local_path
    if (path.suffix.lower() != ".mp4" or path.is_symlink() or not path.is_file()
            or not output.ffprobe_validated):
        raise RuntimeError("ComfyUI output is not a verified MP4")
    size = path.stat().st_size
    if not 0 < size <= 512 * 1024 * 1024 or size != output.size_bytes:
        raise RuntimeError("ComfyUI output size is invalid")
    if sha256(path) != output.sha256:
        raise RuntimeError("ComfyUI output checksum does not match")
    probe = output.media_probe
    if not isinstance(probe, dict):
        raise RuntimeError("ComfyUI output metadata is invalid")
    format_data = probe.get("format")
    streams = probe.get("streams")
    if not isinstance(format_data, dict) or not isinstance(streams, list):
        raise RuntimeError("ComfyUI output metadata is invalid")
    format_names = format_data.get("format_name")
    if not isinstance(format_names, str) or "mp4" not in format_names.split(","):
        raise RuntimeError("ComfyUI output is not an MP4 container")
    videos = [stream for stream in streams if isinstance(stream, dict)
              and stream.get("codec_type") == "video"]
    if len(videos) != 1:
        raise RuntimeError("ComfyUI output must contain one video stream")
    video = videos[0]
    if video.get("codec_name") not in {"h264", "hevc", "av1", "mpeg4"}:
        raise RuntimeError("ComfyUI output uses an unsupported video codec")
    if video.get("width") != spec["width"] or video.get("height") != spec["height"]:
        raise RuntimeError("ComfyUI output dimensions do not match the request")
    try:
        frame_rate = float(Fraction(video.get("avg_frame_rate")))
    except (TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
        raise RuntimeError("ComfyUI output frame rate is invalid") from exc
    if not math.isfinite(frame_rate) or abs(frame_rate - spec["fps"]) > 0.01:
        raise RuntimeError("ComfyUI output frame rate does not match the request")
    duration = format_data.get("duration")
    try:
        seconds = float(duration)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("ComfyUI output duration is invalid") from exc
    expected = spec["frames"] / spec["fps"]
    tolerance = max(2 / spec["fps"], 0.1)
    if not math.isfinite(seconds) or not expected - tolerance <= seconds <= expected + tolerance:
        raise RuntimeError("ComfyUI output duration does not match the request")


def _generate_wan22(job, output_dir):
    spec = _validate_generation_spec(job.spec)
    registry = _workflow_registration()
    work_dir = output_dir / ".comfyui"
    settings = get_settings()
    redis_client = Redis.from_url(REDIS_URL)
    lock = None
    outputs = []
    acquired = False
    destination = output_dir / "generated.mp4"
    provenance_path = output_dir / "provenance.json"
    try:
        work_dir.mkdir(mode=0o700)
        lock = redis_client.lock(
            GPU_LOCK_NAME, timeout=GPU_LOCK_SECONDS, blocking_timeout=GPU_WAIT_SECONDS
        )
        acquired = lock.acquire(blocking=True)
        if not acquired:
            raise RuntimeError("GPU generation capacity is busy")
        with ComfyUIAdapter(
            settings.comfyui_url,
            registry,
            work_dir,
            max_wait_seconds=GPU_WAIT_SECONDS,
            max_output_bytes=512 * 1024 * 1024,
            max_total_output_bytes=512 * 1024 * 1024,
            max_output_count=1,
        ) as adapter:
            submission = adapter.submit("wan22_t2v", spec)
            adapter.wait_for_completion(submission.prompt_id, timeout=GPU_WAIT_SECONDS)
            outputs = adapter.retrieve_outputs(submission)
        if len(outputs) != 1:
            raise RuntimeError("ComfyUI must return exactly one video")
        output = outputs[0]
        _verify_generated_mp4(output, spec)
        output_provenance = output.provenance()
        output_provenance["local_filename"] = "generated.mp4"
        os.replace(output.local_path, destination)
        provenance = {
            "schema_version": 1,
            "job_id": job.id,
            "operation": job.operation,
            "prompt_sha256": hashlib.sha256(spec["positive_prompt"].encode("utf-8")).hexdigest(),
            "generation": {key: spec[key] for key in ("seed", "width", "height", "frames", "fps")},
            "output": output_provenance,
        }
        with provenance_path.open("x", encoding="utf-8") as destination_file:
            json.dump(provenance, destination_file, sort_keys=True, allow_nan=False)
            destination_file.write("\n")
        return f"{job.id}/generated.mp4"
    except Exception:
        destination.unlink(missing_ok=True)
        provenance_path.unlink(missing_ok=True)
        raise
    finally:
        for output in outputs:
            output.local_path.unlink(missing_ok=True)
        shutil.rmtree(work_dir, ignore_errors=True)
        try:
            if acquired and lock is not None:
                lock.release()
        finally:
            redis_client.close()



def _execute_audio_workflow(db, job):
    project = db.get(Project, job.project_id)
    if not project or project.owner != job.owner:
        raise AudioWorkflowError("Project ownership could not be verified")
    spec = job.spec
    if type(spec.get("workflow_version")) is not int or spec["workflow_version"] != WORKFLOW_VERSION:
        raise AudioWorkflowError("Unsupported audio workflow version")
    step = spec.get("step")
    asset = None
    audio_data = None
    if spec.get("audio_asset_id"):
        asset = db.get(AudioAsset, spec["audio_asset_id"])
        if (not asset or asset.project_id != project.id or asset.owner != job.owner):
            raise AudioWorkflowError("Audio asset ownership could not be verified")
        if asset.size_bytes > MAX_AUDIO_BYTES:
            raise AudioWorkflowError("Audio asset exceeds the supported size")
        source = private_file(ARTIFACT_ROOT, asset.relative_path)
        audio_data = source.read_bytes()
        if (len(audio_data) != asset.size_bytes
                or checksum(audio_data) != asset.sha256):
            raise AudioWorkflowError("Audio asset integrity check failed")
        audio_metadata = validate_audio(audio_data)
        if audio_metadata["duration_ms"] != asset.duration_ms:
            raise AudioWorkflowError("Audio asset metadata changed")

    if step == "narration":
        text = spec.get("text")
        language = spec.get("language")
        if (type(text) is not str or not text.strip() or len(text) > 2000
                or type(language) is not str
                or not re.fullmatch(r"[a-z]{2,3}(?:-[A-Z]{2})?", language)):
            raise AudioWorkflowError("Narration request is invalid")
        raise ModelProviderUnavailable
    if step == "transcription":
        if asset is None:
            raise AudioWorkflowError("Transcription requires a project audio asset")
        raise ModelProviderUnavailable
    if step != "captions":
        raise AudioWorkflowError("Unsupported audio workflow step")

    cues = validate_captions(spec.get("captions"))
    if asset and any(cue["end_ms"] > asset.duration_ms for cue in cues):
        raise AudioWorkflowError("Caption timing exceeds the source audio duration")
    formats = spec.get("formats")
    if (type(formats) is not list or not formats or len(formats) > 2
            or len(set(formats)) != len(formats)
            or any(value not in ("srt", "vtt") for value in formats)):
        raise AudioWorkflowError("Subtitle formats are invalid")
    outputs = {f"captions.{fmt}": serialize_subtitles(cues, fmt) for fmt in formats}
    input_manifest = []
    if asset:
        input_manifest.append({
            "asset_id": asset.id,
            "sha256": asset.sha256,
            "size_bytes": asset.size_bytes,
        })
    if "captions" in spec:
        input_manifest.append({
            "kind": "caption_cues",
            "sha256": canonical_hash(cues),
        })
    if type(spec.get("text")) is str:
        input_manifest.append({
            "kind": "narration_text",
            "sha256": hashlib.sha256(spec["text"].encode("utf-8")).hexdigest(),
        })

    artifact_ids = {name: str(uuid.uuid4()) for name in outputs}
    manifest_data = manifest(
        project_id=project.id,
        job_id=job.id,
        inputs=input_manifest,
        outputs=outputs,
        output_ids=artifact_ids,
        step=step,
    )
    manifest_id = str(uuid.uuid4())
    artifact_data = {
        **outputs,
        "manifest.json": manifest_data,
    }
    artifact_records = []
    for filename, data in artifact_data.items():
        artifact_id = manifest_id if filename == "manifest.json" else artifact_ids[filename]
        relative_path = (
            f"projects/{project.id}/artifacts/{job.id}/{filename}"
        )
        write_private_file(ARTIFACT_ROOT, relative_path, data)
        artifact_records.append(WorkflowArtifact(
            id=artifact_id,
            job_id=job.id,
            project_id=project.id,
            owner=job.owner,
            relative_path=relative_path,
            filename=filename,
            media_type=("application/json" if filename == "manifest.json"
                        else "text/vtt" if filename.endswith(".vtt")
                        else "application/x-subrip"),
            sha256=checksum(data),
            size_bytes=len(data),
        ))
    db.add_all(artifact_records)
    transition(db, job, "awaiting_review", "worker", artifact=manifest_id)


def _cleanup_render_output(inputs, result):
    try:
        path = Path(result.path)
        workspace = path.parent
        if (path.name != 'output.mp4' or not workspace.name.startswith('render-')
                or workspace.is_symlink()
                or workspace.parent.resolve(strict=True) != inputs.resolve(strict=True)):
            return
        path.unlink(missing_ok=True)
        workspace.rmdir()
    except (AttributeError, OSError, RuntimeError, TypeError):
        pass


def _finish_or_cancel(
    db,
    job,
    status,
    artifact=None,
    artifact_id=None,
    artifact_sha256=None,
):
    if not transition(
        db,
        job,
        status,
        'worker',
        artifact=artifact,
        artifact_id=artifact_id,
        artifact_sha256=artifact_sha256,
    ):
        if job.status == 'cancel_requested':
            transition(db, job, 'cancelled', 'worker')


def _stage_timeline_inputs(db, job):
    project = db.get(Project, job.project_id)
    user = db.scalar(select(User).where(User.username == job.owner))
    membership = db.scalar(select(ProjectMembership).where(
        ProjectMembership.project_id == job.project_id,
        ProjectMembership.user_id == user.id if user else False,
    ))
    if not project or not user or user.disabled or not membership or membership.role not in {"owner", "editor"}:
        raise ValueError("Render project authorization no longer valid")
    inputs = ARTIFACT_ROOT / job.id / "inputs"
    if inputs.exists() or inputs.is_symlink():
        raise ValueError("Job staging already exists")
    manifest = job.spec["input_manifest"]
    if (not isinstance(manifest, list) or not 1 <= len(manifest) <= 17
            or sum(item["size_bytes"] for item in manifest) > 512 * 1024 * 1024
            or shutil.disk_usage(ARTIFACT_ROOT).free < 768 * 1024 * 1024):
        raise ValueError("Render input or disk quota exceeded")
    for item in manifest:
        asset = db.get(MediaAsset, item["id"])
        if (not asset or asset.project_id != job.project_id
                or asset.sha256 != item["sha256"] or asset.size_bytes != item["size_bytes"]
                or asset.storage_key != item["storage_key"]
                or item["path"] != f"{asset.id}{Path(asset.storage_key).suffix.lower()}"):
            raise ValueError("Render input identity changed")
        source = private_file(ARTIFACT_ROOT, asset.storage_key)
        if source.stat().st_size != asset.size_bytes or sha256(source) != asset.sha256:
            raise ValueError("Render input integrity check failed")
        write_private_file(ARTIFACT_ROOT, f"{job.id}/inputs/{item['path']}", source.read_bytes())
        if sha256(private_file(ARTIFACT_ROOT, f"{job.id}/inputs/{item['path']}")) != asset.sha256:
            raise ValueError("Input changed while staging")
    if "title" in job.spec["timeline"] or job.spec["timeline"].get("captions"):
        font = Path("/usr/share/fonts/dejavu/DejaVuSans.ttf")
        write_private_file(ARTIFACT_ROOT, f"{job.id}/inputs/font.ttf", font.read_bytes())


def execute(job_id: str):
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job or job.status not in ('queued', 'dispatch_pending', 'cancel_requested'):
            return
        if job.status == 'cancel_requested':
            transition(db, job, 'cancelled', 'worker')
            return
        while not transition(db, job, 'running', 'worker'):
            if job.status not in ('queued', 'dispatch_pending'):
                return
        result = None
        provenance_path = None
        provenance_created = False
        inputs = None
        try:
            if _cancel_requested(job_id):
                raise JobCancelled()
            if job.operation == 'render_timeline_v1':
                if str(uuid.UUID(job.id)) != job.id:
                    raise ValueError('Invalid job identity')
                root = ARTIFACT_ROOT.resolve(strict=True)
                if "input_manifest" in job.spec:
                    _stage_timeline_inputs(db, job)
                output_dir = root / job.id
                inputs = output_dir / 'inputs'
                if (output_dir.is_symlink() or inputs.is_symlink()
                        or not inputs.resolve(strict=True).is_relative_to(root / job.id)):
                    raise ValueError('Invalid staged input directory')
                last_heartbeat = 0

                def check_cancel():
                    nonlocal last_heartbeat
                    now = time.monotonic()
                    if now - last_heartbeat >= 5:
                        with SessionLocal() as heartbeat_db:
                            heartbeat_db.execute(update(Job).where(
                                Job.id == job_id, Job.status == "running",
                            ).values(updated_at=datetime.now(timezone.utc)))
                            heartbeat_db.commit()
                        last_heartbeat = now
                    return _cancel_requested(job_id)

                engine = RenderEngine(inputs, font='font.ttf' if (inputs / 'font.ttf').exists() else None,
                                      cancel=check_cancel)
                result = engine.render(json.dumps(job.spec['timeline'], ensure_ascii=False,
                                                  separators=(',', ':')))
                provenance_path = output_dir / 'provenance.json'
                fd = os.open(provenance_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                provenance_created = True
                with os.fdopen(fd, 'w', encoding='utf-8') as file:
                    json.dump(result.provenance, file, sort_keys=True, indent=2)
                artifact_id = str(uuid.uuid4())
                artifact_key = str(result.path.relative_to(root))
                output_checksum = result.provenance["output_sha256"]
                if job.project_id:
                    db.add(WorkflowArtifact(
                        id=artifact_id, job_id=job.id, project_id=job.project_id,
                        owner=job.owner, relative_path=artifact_key, filename="video.mp4",
                        media_type="video/mp4", sha256=output_checksum,
                        size_bytes=result.path.stat().st_size,
                    ))
                _finish_or_cancel(
                    db, job, 'awaiting_review',
                    artifact=artifact_key, artifact_id=artifact_id,
                    artifact_sha256=output_checksum,
                )
                if job.status == 'cancelled':
                    _cleanup_render_output(inputs, result)
                    if provenance_created:
                        try:
                            provenance_path.unlink(missing_ok=True)
                        except OSError:
                            pass
                return
            if job.operation == "audio_workflow":
                _execute_audio_workflow(db, job)
            elif job.operation == 'render_test':
                if Path(job.id).name != job.id or job.id in {'.', '..'}:
                    raise ValueError('Invalid artifact identifier')
                artifact_root = ARTIFACT_ROOT.resolve()
                output_dir = artifact_root / job.id
                if output_dir.is_symlink() or output_dir.resolve().parent != artifact_root:
                    raise ValueError('Invalid artifact directory')
                output_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(
                    prefix='.work-', dir=output_dir
                ) as work_dir:
                    temp_dest = Path(work_dir) / 'test.mp4'
                    _run_ffmpeg(job.id, [
                        'ffmpeg', '-nostdin', '-y', '-f', 'lavfi', '-i',
                        'testsrc2=size=640x360:rate=24', '-t', '5',
                        '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                        '-movflags', '+faststart', str(temp_dest),
                    ], timeout=120)
                    artifact = f'{job.id}/test.mp4'
                    os.replace(temp_dest, output_dir / 'test.mp4')
                    output_checksum = generated_checksum(job, artifact=artifact)
                    checksum_path = Path(work_dir) / 'SHA256SUMS'
                    checksum_path.write_text(f'{output_checksum}  test.mp4\n')
                    os.replace(checksum_path, output_dir / 'SHA256SUMS')
                _finish_or_cancel(
                    db,
                    job,
                    'awaiting_review',
                    artifact=artifact,
                    artifact_id=str(uuid.uuid4()),
                    artifact_sha256=output_checksum,
                )
            elif job.operation == 'wan22_t2v':
                if _cancel_requested(job_id):
                    raise JobCancelled()
                if not get_settings().comfyui_enabled:
                    raise RuntimeError("ComfyUI generation is disabled")
                output_dir = ARTIFACT_ROOT / job.id
                output_dir.mkdir(mode=0o750, parents=True, exist_ok=False)
                artifact = _generate_wan22(job, output_dir)
                _finish_or_cancel(db, job, 'awaiting_review', artifact=artifact)
            else:
                raise RuntimeError("Unsupported operation")
        except JobCancelled:
            db.refresh(job)
            if job.status == 'running':
                transition(db, job, 'cancel_requested', 'worker')
            if job.status == 'cancel_requested':
                transition(db, job, 'cancelled', 'worker')
        except Exception as exc:
            db.rollback()
            db.refresh(job)
            if job.operation == 'render_timeline_v1':
                if result is not None and inputs is not None:
                    _cleanup_render_output(inputs, result)
                if provenance_created:
                    try:
                        provenance_path.unlink(missing_ok=True)
                    except OSError:
                        pass
            if job.status == 'cancel_requested':
                transition(db, job, 'cancelled', 'worker')
                return
            if job.operation == "audio_workflow":
                if isinstance(exc, ModelProviderUnavailable):
                    error = "provider_unavailable; NOT_VERIFIED"
                elif isinstance(exc, AudioWorkflowError):
                    error = f"invalid_audio_workflow: {str(exc)[:200]}"
                else:
                    error = "audio_workflow_failed"
            elif job.operation == "wan22_t2v":
                error = "ComfyUI generation failed; inspect restricted worker diagnostics."
            elif job.operation == 'render_timeline_v1':
                error = f'{type(exc).__name__}: timeline render failed'
            else:
                error = "Job execution failed; details withheld"
            try:
                transitioned = transition(db, job, 'failed', 'worker', error=error)
            except Exception:
                if job.operation == 'render_timeline_v1':
                    raise
                raise RuntimeError("job execution failed") from None
            if not transitioned:
                if job.status == 'cancel_requested':
                    transition(db, job, 'cancelled', 'worker')
                    return
            if job.operation in {"render_test", "wan22_t2v"} or error == "Job execution failed; details withheld":
                raise RuntimeError("job execution failed") from None
            raise
        finally:
            if (job.operation == "render_timeline_v1" and "input_manifest" in job.spec
                    and job.status in {"awaiting_review", "failed", "cancelled"}):
                for item in job.spec["input_manifest"]:
                    try:
                        private_file(ARTIFACT_ROOT, f"{job.id}/inputs/{item['path']}").unlink()
                    except (OSError, ValueError):
                        logger.warning("Staged input cleanup unavailable for job %s", job.id)
                font = ARTIFACT_ROOT / job.id / "inputs" / "font.ttf"
                try:
                    if font.is_file() and not font.is_symlink():
                        font.unlink()
                except OSError:
                    logger.warning("Staged font cleanup unavailable for job %s", job.id)


def main():
    connection = Redis.from_url(REDIS_URL)
    job_queue = Queue("video", connection=connection, default_timeout=3600)
    with SessionLocal() as db:
        recover_jobs(db, job_queue)
    Worker([job_queue], connection=connection).work()


if __name__ == "__main__":
    main()
