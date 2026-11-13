import hashlib
import json

import httpx
import pytest

from app.comfyui import (
    ComfyUIAdapter,
    ComfyUIMalformedResponseError,
    WorkflowRegistry,
)


def registry():
    graph = {
        "1": {
            "class_type": "TestNode",
            "inputs": {"text": "", "seed": 0},
        }
    }
    workflows = WorkflowRegistry()
    workflows.register(
        "test",
        graph,
        {"prompt": ("1", "text"), "seed": ("1", "seed")},
    )
    return workflows


def history(prompt_id="prompt-1"):
    return {
        prompt_id: {
            "status": {"completed": True, "status_str": "success"},
            "outputs": {
                "9": {
                    "videos": [
                        {"filename": "generated.mp4", "subfolder": "", "type": "output"}
                    ]
                }
            },
        }
    }


def test_adapter_submits_polls_and_retrieves_registered_video(tmp_path):
    seen = []

    def handle(request):
        seen.append(request)
        if request.method == "POST":
            payload = json.loads(request.content)
            assert payload["prompt"]["1"]["inputs"] == {"text": "a prompt", "seed": 42}
            return httpx.Response(200, json={"prompt_id": "prompt-1"})
        if request.url.path == "/history/prompt-1":
            return httpx.Response(200, json=history())
        assert request.url.path == "/view"
        assert request.url.params["filename"] == "generated.mp4"
        return httpx.Response(200, content=b"synthetic MP4")

    client = httpx.Client(
        base_url="http://comfyui:8188",
        transport=httpx.MockTransport(handle),
    )
    adapter = ComfyUIAdapter(
        "http://comfyui:8188",
        registry(),
        tmp_path,
        client=client,
        max_output_count=1,
    )
    adapter._probe_media = lambda _path: {
        "format": {"format_name": "mov,mp4,m4a", "duration": "2"},
        "streams": [{"codec_type": "video", "codec_name": "h264", "width": 512, "height": 288}],
    }

    submission = adapter.submit("test", {"prompt": "a prompt", "seed": 42})
    assert adapter.wait_for_completion(submission.prompt_id, timeout=1).state == "succeeded"
    outputs = adapter.retrieve_outputs(submission)

    assert len(outputs) == 1
    result = outputs[0]
    assert result.local_path.read_bytes() == b"synthetic MP4"
    assert result.sha256 == hashlib.sha256(b"synthetic MP4").hexdigest()
    assert result.ffprobe_validated
    assert len([request for request in seen if request.url.path == "/history/prompt-1"]) == 2
    result.local_path.unlink()
    client.close()


def test_adapter_rejects_public_comfyui_hosts_and_unsafe_output(tmp_path):
    with pytest.raises(ValueError, match="private"):
        ComfyUIAdapter("https://example.com", registry(), tmp_path)

    with pytest.raises(ComfyUIMalformedResponseError, match="unsafe"):
        ComfyUIAdapter._output_descriptors(
            {"1": {"videos": [{"filename": "../outside.mp4", "type": "output"}]}}
        )


def test_ffprobe_uses_bounded_runner(monkeypatch, tmp_path):
    from app import comfyui

    adapter = ComfyUIAdapter("http://comfyui:8188", registry(), tmp_path)
    path = tmp_path / "generated.mp4"
    path.write_bytes(b"synthetic")
    calls = []

    def fake_run(command, cwd, timeout):
        calls.append((command, cwd, timeout))
        return b'{"streams":[{"codec_type":"video"}],"format":{"format_name":"mp4"}}'

    monkeypatch.setattr(comfyui.shutil, "which", lambda _name: "/usr/bin/ffprobe")
    monkeypatch.setattr(comfyui, "_run", fake_run)

    assert adapter._probe_media(path)["format"]["format_name"] == "mp4"
    assert calls[0][0][0] == "/usr/bin/ffprobe"
    assert calls[0][1] == tmp_path
    assert calls[0][2] == adapter.ffprobe_timeout
