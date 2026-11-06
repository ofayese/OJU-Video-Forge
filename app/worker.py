import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
import uuid
from fractions import Fraction
from pathlib import Path

from redis import Redis
from rq import Worker

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
    REDIS_URL,
    SessionLocal,
    WorkflowArtifact,
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
            if job.operation == "audio_workflow":
                _execute_audio_workflow(db, job)
            elif job.operation == 'render_test':
                output_dir = ARTIFACT_ROOT / job.id
                output_dir.mkdir(mode=0o750, parents=True, exist_ok=False)
                dest = output_dir / 'test.mp4'
                _run_ffmpeg(job.id, [
                    'ffmpeg', '-nostdin', '-y', '-f', 'lavfi', '-i',
                    'testsrc2=size=640x360:rate=24', '-t', '5',
                    '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                    '-movflags', '+faststart', str(dest),
                ], timeout=120)
                artifact = f'{job.id}/test.mp4'
                checksum = generated_checksum(job, artifact=artifact)
                (output_dir / 'SHA256SUMS').write_text(f'{checksum}  test.mp4\n')
                transition(db, job, 'awaiting_review', 'worker', artifact=artifact,
                           artifact_id=str(uuid.uuid4()), artifact_sha256=checksum)
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
                error = type(exc).__name__
            if not transition(db, job, 'failed', 'worker', error=error):
                if job.status == 'cancel_requested':
                    transition(db, job, 'cancelled', 'worker')
                    return
            if job.operation == "wan22_t2v":
                raise RuntimeError("ComfyUI generation failed") from None
            raise


if __name__ == "__main__":
    Worker(["video"], connection=Redis.from_url(REDIS_URL)).work()
