import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app import worker
from app.comfyui import RetrievedOutput, Submission
from app.config import Settings


def generation_spec(**changes):
    spec = {
        "prompt": "a synthetic test prompt",
        "seed": 42,
        "width": 512,
        "height": 288,
        "frames": 33,
        "fps": 16,
    }
    spec.update(changes)
    return spec


class FakeLock:
    def __init__(self):
        self.acquired = False
        self.released = False

    def acquire(self, *, blocking):
        assert blocking is True
        self.acquired = True
        return True

    def release(self):
        self.released = True


class FakeRedis:
    def __init__(self):
        self.lock_value = FakeLock()
        self.closed = False

    def lock(self, name, *, timeout, blocking_timeout):
        assert name == worker.GPU_LOCK_NAME
        assert timeout == worker.GPU_LOCK_SECONDS
        assert blocking_timeout == worker.GPU_WAIT_SECONDS
        return self.lock_value

    def close(self):
        self.closed = True


class FakeAdapter:
    def __init__(self, _url, _registry, output_dir, **_kwargs):
        self.output_dir = output_dir
        self.submitted_inputs = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def submit(self, workflow_id, inputs):
        assert workflow_id == "wan22_t2v"
        self.submitted_inputs = inputs
        return Submission("prompt-1", workflow_id, "a" * 64)

    def wait_for_completion(self, prompt_id, *, timeout):
        assert prompt_id == "prompt-1"
        assert timeout == worker.GPU_WAIT_SECONDS

    def retrieve_outputs(self, _submission):
        output_path = self.output_dir / "output.mp4"
        output_path.write_bytes(b"synthetic MP4")
        probe = {
            "format": {
                "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
                "duration": str(33 / 16),
            },
            "streams": [
                {
                    "codec_type": "video",
                    "width": 512,
                    "height": 288,
                    "codec_name": "h264",
                    "avg_frame_rate": "16/1",
                }
            ],
        }
        return [
            RetrievedOutput(
                local_path=output_path,
                source_filename="output.mp4",
                source_subfolder="",
                size_bytes=output_path.stat().st_size,
                sha256=hashlib.sha256(output_path.read_bytes()).hexdigest(),
                workflow_id="wan22_t2v",
                workflow_sha256="a" * 64,
                prompt_id="prompt-1",
                comfyui_base_url="http://comfyui:8188",
                retrieved_at=datetime.now(timezone.utc),
                media_probe=probe,
            )
        ]


def test_worker_serializes_and_registers_verified_output(monkeypatch, tmp_path):
    graph = {
        "1": {
            "class_type": "TestNode",
            "inputs": {
                name: ("" if name == "positive_prompt" else 0)
                for name in worker.GPU_INPUTS
            },
        }
    }
    workflow_path = tmp_path / "workflow.json"
    workflow_path.write_text(json.dumps(graph), encoding="utf-8")
    mapping = {name: ["1", name] for name in worker.GPU_INPUTS}
    monkeypatch.setattr(worker, "WORKFLOW", workflow_path)
    monkeypatch.setenv("WAN_NODE_MAPPING", json.dumps(mapping))
    monkeypatch.setattr(worker, "get_settings", lambda: Settings(comfyui_enabled=True))

    redis_client = FakeRedis()
    monkeypatch.setattr(worker.Redis, "from_url", lambda _url: redis_client)
    adapter_instances = []

    def fake_adapter(*args, **kwargs):
        instance = FakeAdapter(*args, **kwargs)
        adapter_instances.append(instance)
        return instance

    monkeypatch.setattr(worker, "ComfyUIAdapter", fake_adapter)
    job = SimpleNamespace(id="job-1", operation="wan22_t2v", spec=generation_spec())
    output_dir = tmp_path / "artifacts" / job.id
    output_dir.mkdir(parents=True)

    artifact = worker._generate_wan22(job, output_dir)

    assert artifact == "job-1/generated.mp4"
    assert (output_dir / "generated.mp4").read_bytes() == b"synthetic MP4"
    provenance = json.loads((output_dir / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["generation"] == {
        "seed": 42, "width": 512, "height": 288, "frames": 33, "fps": 16,
    }
    assert provenance["prompt_sha256"] == hashlib.sha256(
        b"a synthetic test prompt"
    ).hexdigest()
    assert provenance["output"]["local_filename"] == "generated.mp4"
    assert adapter_instances[0].submitted_inputs["positive_prompt"] == "a synthetic test prompt"
    assert redis_client.lock_value.acquired and redis_client.lock_value.released
    assert redis_client.closed
    assert not (output_dir / ".comfyui").exists()


@pytest.mark.parametrize(
    "spec",
    [
        generation_spec(width=True),
        generation_spec(width=513),
        generation_spec(frames=32),
        generation_spec(fps=12),
        generation_spec(prompt=" "),
    ],
)
def test_worker_rejects_invalid_generation_settings(spec):
    with pytest.raises(ValueError):
        worker._validate_generation_spec(spec)
