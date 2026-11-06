import os
import json
import hashlib
import subprocess
import time
import uuid
from pathlib import Path
import httpx
from redis import Redis
from rq import Worker
from .core import SessionLocal, ARTIFACT_ROOT, REDIS_URL, transition, Job
from .rendering import RenderEngine

COMFYUI_URL = os.environ.get('COMFYUI_URL', 'http://comfyui:8188')
WORKFLOW = Path('/workflows/wan22-t2v-api.json')


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
            spec = job.spec
            if job.operation == 'render_test':
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
            error = (f'{type(exc).__name__}: timeline render failed'
                     if job.operation == 'render_timeline_v1'
                     else type(exc).__name__)
            if not transition(db, job, 'failed', 'worker', error=error):
                if job.status == 'cancel_requested':
                    transition(db, job, 'cancelled', 'worker')
                    return
            raise


if __name__ == "__main__":
    Worker(["video"], connection=Redis.from_url(REDIS_URL)).work()
