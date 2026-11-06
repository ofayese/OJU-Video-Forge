import os
import json
import hashlib
import subprocess
import uuid
from pathlib import Path
import httpx
from sqlalchemy import update
from .core import SessionLocal, ARTIFACT_ROOT, transition, Job, Audit
from .rendering import RenderEngine

COMFYUI_URL = os.environ.get('COMFYUI_URL', 'http://comfyui:8188')
WORKFLOW = Path('/workflows/wan22-t2v-api.json')

def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def execute(job_id: str):
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job or job.status not in ('queued','dispatch_pending'):
            return
        if job.operation == 'render_timeline_v1':
            claimed = db.execute(
                update(Job).where(Job.id == job_id, Job.status.in_(('queued', 'dispatch_pending')))
                .values(status='running')
            ).rowcount
            if not claimed:
                db.rollback()
                return
            db.add(Audit(job_id=job_id, actor='worker', action='running'))
            db.commit()
        else:
            transition(db, job, 'running', 'worker')
        try:
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
                with os.fdopen(fd, 'w', encoding='utf-8') as file:
                    json.dump(result.provenance, file, sort_keys=True, indent=2)
                transition(db, job, 'awaiting_review', 'worker',
                           artifact=str(result.path.relative_to(root)))
                return
            output_dir = ARTIFACT_ROOT / job.id
            output_dir.mkdir(mode=0o750, parents=True, exist_ok=False)
            spec = job.spec
            if job.operation == 'render_test':
                dest = output_dir / 'test.mp4'
                subprocess.run([
                    'ffmpeg','-nostdin','-y','-f','lavfi','-i',
                    'testsrc2=size=640x360:rate=24','-t','5',
                    '-an','-c:v','libx264','-pix_fmt','yuv420p',
                    '-movflags','+faststart',str(dest)
                ], check=True, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                (output_dir / 'SHA256SUMS').write_text(f'{sha256(dest)}  test.mp4\n')
                transition(db, job, 'awaiting_review', 'worker', artifact=f'{job.id}/test.mp4')
            elif job.operation == 'wan22_t2v':
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
                (output_dir / 'comfy-prompt.json').write_text(json.dumps({'prompt_id':prompt_id}, indent=2))
                # The initial gate verifies submission; no claim of completed video until
                # a history/output reconciler validates and copies produced files.
                transition(db, job, 'submitted_to_comfyui', 'worker', artifact=f'{job.id}/comfy-prompt.json')
            else:
                raise RuntimeError('Unsupported operation')
        except Exception as exc:
            error = (f'{type(exc).__name__}: timeline render failed'
                     if job.operation == 'render_timeline_v1'
                     else f'{type(exc).__name__}: {str(exc)[:300]}')
            transition(db, job, 'failed', 'worker', error=error)
            raise
