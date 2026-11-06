import os
import json
import hashlib
import uuid
import subprocess
from pathlib import Path
import httpx
from .core import SessionLocal, ARTIFACT_ROOT, transition, Job
from .review import generated_checksum

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
        if not transition(db, job, 'running', 'worker'):
            return
        try:
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
                artifact = f'{job.id}/test.mp4'
                checksum = generated_checksum(job, artifact=artifact)
                (output_dir / 'SHA256SUMS').write_text(f'{checksum}  test.mp4\n')
                transition(db, job, 'awaiting_review', 'worker', artifact=artifact,
                           artifact_id=str(uuid.uuid4()), artifact_sha256=checksum)
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
            transition(db, job, 'failed', 'worker', error=f'{type(exc).__name__}: {str(exc)[:300]}')
            raise
