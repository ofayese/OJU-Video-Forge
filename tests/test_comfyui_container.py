from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_comfyui_identity_and_private_compose_mounts():
    dockerfile = (ROOT / "services/comfyui/Dockerfile").read_text()
    assert "groupadd --gid 10001 forge" in dockerfile
    assert "useradd --create-home --uid 10001 --gid 10001 forge" in dockerfile
    assert dockerfile.split("USER ")[-1].splitlines()[0] == "10001:10001"
    assert '"--temp-directory","/home/forge"' in dockerfile
    assert '"--previews-directory","/home/forge/previews"' in dockerfile

    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    comfy = compose["services"]["comfyui"]
    assert comfy["profiles"] == ["gpu"]
    assert comfy["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in comfy["security_opt"]
    assert comfy["networks"] == ["inference"]
    assert "ports" not in comfy
    assert comfy["volumes"] == [
        "${FORGE_DATA:-/srv/video-forge}/models:/opt/ComfyUI/models",
        "${FORGE_DATA:-/srv/video-forge}/comfy-input:/opt/ComfyUI/input",
        "${FORGE_DATA:-/srv/video-forge}/comfy-output:/opt/ComfyUI/output",
    ]
