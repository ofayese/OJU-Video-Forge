import hashlib
import json
import math
import os
import shutil
import subprocess
import time
import uuid
from fractions import Fraction
from pathlib import Path

from redis import Redis
from rq import Worker

from .comfyui import ComfyUIAdapter, WorkflowRegistry
from .config import get_settings
from .core import ARTIFACT_ROOT, REDIS_URL, SessionLocal, Job, transition
from .rendering import RenderEngine

WORKFLOW = Path("/workflows/wan22-t2v-api.json")
GPU_LOCK_NAME = "oju-video-forge:comfyui:generation"
GPU_LOCK_SECONDS = 7200
GPU_WAIT_SECONDS = 3000
GPU_INPUTS = {"positive_prompt", "seed", "width", "height", "frames", "fps"}
MAX_WORKFLOW_BYTES = 16 * 1024 * 1024



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


def _finish_or_cancel(db, job, status, artifact=None):
    if not transition(db, job, status, 'worker', artifact=artifact):
        if job.status == 'cancel_requested':
            transition(db, job, 'cancelled', 'worker')


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
                output_dir = root / job.id
                inputs = output_dir / 'inputs'
                if (output_dir.is_symlink() or inputs.is_symlink()
                        or not inputs.resolve(strict=True).is_relative_to(root / job.id)):
                    raise ValueError('Invalid staged input directory')
                engine = RenderEngine(inputs, font='font.ttf' if (inputs / 'font.ttf').exists() else None)
                result = engine.render(json.dumps(job.spec['timeline'], ensure_ascii=False,
                                                  separators=(',', ':')))
                provenance_path = output_dir / 'provenance.json'
                fd = os.open(provenance_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                provenance_created = True
                with os.fdopen(fd, 'w', encoding='utf-8') as file:
                    json.dump(result.provenance, file, sort_keys=True, indent=2)
                _finish_or_cancel(
                    db, job, 'awaiting_review',
                    artifact=str(result.path.relative_to(root)),
                )
                if job.status == 'cancelled':
                    _cleanup_render_output(inputs, result)
                    if provenance_created:
                        try:
                            provenance_path.unlink(missing_ok=True)
                        except OSError:
                            pass
                return
            output_dir = ARTIFACT_ROOT / job.id
            output_dir.mkdir(mode=0o750, parents=True, exist_ok=False)
            if job.operation == 'render_test':
                dest = output_dir / "test.mp4"
                _run_ffmpeg(job.id, [
                    'ffmpeg', '-nostdin', '-y', '-f', 'lavfi', '-i',
                    'testsrc2=size=640x360:rate=24', '-t', '5',
                    '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                    '-movflags', '+faststart', str(dest),
                ], timeout=120)
                (output_dir / "SHA256SUMS").write_text(f"{sha256(dest)}  test.mp4\n")
                _finish_or_cancel(
                    db, job, 'awaiting_review', artifact=f"{job.id}/test.mp4"
                )
            elif job.operation == 'wan22_t2v':
                if not get_settings().comfyui_enabled:
                    raise RuntimeError("ComfyUI generation is disabled")
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
            error = (
                "ComfyUI generation failed; inspect restricted worker diagnostics."
                if job.operation == "wan22_t2v"
                else f'{type(exc).__name__}: timeline render failed'
                if job.operation == 'render_timeline_v1'
                else type(exc).__name__
            )
            if not transition(db, job, 'failed', 'worker', error=error):
                if job.status == 'cancel_requested':
                    transition(db, job, 'cancelled', 'worker')
                    return
            if job.operation == "wan22_t2v":
                raise RuntimeError("ComfyUI generation failed") from None
            raise


if __name__ == "__main__":
    Worker(["video"], connection=Redis.from_url(REDIS_URL)).work()
