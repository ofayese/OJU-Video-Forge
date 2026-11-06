import os
import json
import hashlib
import subprocess
import time
import uuid
from pathlib import Path
import re
import httpx
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
from .rendering import RenderEngine

COMFYUI_URL = os.environ.get('COMFYUI_URL', 'http://comfyui:8188')
WORKFLOW = Path('/workflows/wan22-t2v-api.json')


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
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


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
            spec = job.spec
            if job.operation == "audio_workflow":
                _execute_audio_workflow(db, job)
            elif job.operation == 'render_test':
                output_dir = ARTIFACT_ROOT / job.id
                output_dir.mkdir(mode=0o750, parents=True, exist_ok=False)
                dest = output_dir / 'test.mp4'
                _run_ffmpeg(job.id, [
                    'ffmpeg','-nostdin','-y','-f','lavfi','-i',
                    'testsrc2=size=640x360:rate=24','-t','5',
                    '-an','-c:v','libx264','-pix_fmt','yuv420p',
                    '-movflags','+faststart',str(dest)
                ], timeout=120)
                (output_dir / 'SHA256SUMS').write_text(f'{sha256(dest)}  test.mp4\n')
                _finish_or_cancel(
                    db, job, 'awaiting_review', artifact=f'{job.id}/test.mp4'
                )
            elif job.operation == 'wan22_t2v':
                if _cancel_requested(job_id):
                    raise JobCancelled()
                output_dir = ARTIFACT_ROOT / job.id
                output_dir.mkdir(mode=0o750, parents=True, exist_ok=False)
                if not WORKFLOW.is_file():
                    raise RuntimeError('Approved API workflow missing; import and validate before GPU gate')
                graph = json.loads(WORKFLOW.read_text())
                # These exact IDs are established by an administrator after API export.
                mapping = json.loads(os.environ.get('WAN_NODE_MAPPING', '{}'))
                if not {'positive_prompt','seed'} <= mapping.keys():
                    raise RuntimeError('WAN_NODE_MAPPING not configured')
                for field, value in (('positive_prompt', spec['prompt']), ('seed', spec['seed'])):
                    node, key = mapping[field]
                    if node not in graph or key not in graph[node]['inputs']:
                        raise RuntimeError('Invalid approved node mapping')
                    graph[node]['inputs'][key] = value
                with httpx.Client(timeout=30) as client:
                    r = client.post(COMFYUI_URL + '/prompt', json={'prompt': graph, 'client_id':'video-forge'})
                    r.raise_for_status()
                    prompt_id = r.json()['prompt_id']
                if _cancel_requested(job_id):
                    raise JobCancelled()
                (output_dir / 'comfy-prompt.json').write_text(json.dumps({'prompt_id':prompt_id}, indent=2))
                # The initial gate verifies submission; no claim of completed video until
                # a history/output reconciler validates and copies produced files.
                _finish_or_cancel(
                    db,
                    job,
                    'submitted_to_comfyui',
                    artifact=f'{job.id}/comfy-prompt.json',
                )
            else:
                raise RuntimeError('Unsupported operation')
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
            if job.operation == "audio_workflow":
                if isinstance(exc, ModelProviderUnavailable):
                    error = "provider_unavailable; NOT_VERIFIED"
                elif isinstance(exc, AudioWorkflowError):
                    error = f"invalid_audio_workflow: {str(exc)[:200]}"
                else:
                    error = "audio_workflow_failed"
            elif job.operation == 'render_timeline_v1':
                error = f'{type(exc).__name__}: timeline render failed'
            else:
                error = type(exc).__name__
            if not transition(db, job, 'failed', 'worker', error=error):
                if job.status == 'cancel_requested':
                    transition(db, job, 'cancelled', 'worker')
                    return
            raise


if __name__ == "__main__":
    Worker(["video"], connection=Redis.from_url(REDIS_URL)).work()
