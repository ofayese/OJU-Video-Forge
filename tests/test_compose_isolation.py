from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class ComposeLoader(yaml.SafeLoader):
    pass


class OverrideSequence(list):
    pass


ComposeLoader.add_constructor(
    "!override",
    lambda loader, node: OverrideSequence(loader.construct_sequence(node)),
)


def test_environments_have_distinct_identity_and_loopback_ports():
    base = yaml.safe_load((ROOT / "compose.yaml").read_text())
    assert base["services"]["api"]["environment"]["FORGE_OPENWEBUI_TOKEN"] == "${FORGE_OPENWEBUI_TOKEN:-}"
    assert base["services"]["api"]["environment"]["FORGE_CHATGPT_TOKEN"] == "${FORGE_CHATGPT_TOKEN:-}"
    assert "FORGE_OPENWEBUI_TOKEN" not in base["services"]["render-worker"].get("environment", {})
    assert "FORGE_CHATGPT_TOKEN" not in base["services"]["render-worker"].get("environment", {})
    dev = yaml.safe_load((ROOT / "compose.dev.yaml").read_text())
    assert dev["services"]["frontend"]["read_only"] is False
    for template in (".env.example", ".env.development.example", ".env.production.example"):
        environment = dict(
            line.split("=", 1)
            for line in (ROOT / template).read_text().splitlines()
            if line.startswith(("FORGE_OPENWEBUI_TOKEN=", "FORGE_CHATGPT_TOKEN="))
        )
        assert environment == {"FORGE_OPENWEBUI_TOKEN": "", "FORGE_CHATGPT_TOKEN": ""}
    assert base["name"] == "oju-video-forge"
    assert base["services"]["frontend"]["ports"] == ["127.0.0.1:5173:8080"]
    names = {base["name"]}
    for environment, port in (("acceptance", 5174), ("integration", 5175)):
        overlay = yaml.load((ROOT / f"compose.{environment}.yaml").read_text(), Loader=ComposeLoader)
        assert overlay["name"] == f"oju-video-forge-{environment}"
        names.add(overlay["name"])
        assert overlay["services"]["frontend"]["ports"] == [f"127.0.0.1:{port}:8080"]
        assert isinstance(overlay["services"]["frontend"]["ports"], OverrideSequence)
        assert overlay["services"]["api"]["environment"]["APP_ENV"] == "test"
        assert overlay["services"]["api"]["environment"]["FORGE_TEST_PROJECT"] == overlay["name"]
    assert len(names) == 3
    assert all("container_name" not in service for service in base["services"].values())
    assert all(not network.get("external") for network in base["networks"].values())
    assert all(not volume.get("external") and "name" not in volume for volume in base["volumes"].values())
    for service in ("postgres", "redis", "comfyui"):
        assert "ports" not in base["services"][service]


def test_narration_ocr_is_private_and_resource_bounded():
    base = yaml.safe_load((ROOT / "compose.yaml").read_text())
    overlay = yaml.safe_load((ROOT / "compose.narration.yaml").read_text())
    worker = overlay["services"]["render-worker"]
    ocr = overlay["services"]["docling-ocr"]

    assert set(worker["networks"]) == {"control", "inference", "narration-ocr"}
    assert overlay["networks"]["narration-ocr"] == {"internal": True}
    assert set(ocr["networks"]) == {"narration-ocr"}
    assert not set(ocr["networks"]) & {"control", "inference"}
    assert ocr["image"] == "oju-video-forge/docling-ocr:1.38.2"
    assert "ports" not in ocr
    assert "volumes" not in ocr
    assert "devices" not in ocr
    assert ocr["read_only"] is True
    assert ocr["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in ocr["security_opt"]
    assert ocr["cpus"] == 0.5
    assert ocr["mem_limit"] == "2g"
    assert ocr["pids_limit"] == 64
    assert ocr["healthcheck"]["retries"] == 10
    assert worker["depends_on"]["docling-ocr"]["condition"] == "service_healthy"
    assert worker["volumes"] == ["narration-models:/models/asr:ro"]
    assert overlay["volumes"] == {"narration-models": {}}
    assert base["networks"]["control"]["internal"] is True
    assert base["networks"]["inference"]["internal"] is True

    environment = ocr["environment"]
    assert environment["DOCLING_SERVE_ALLOW_CUSTOM_OCR_CONFIG"] == "false"
    assert environment["DOCLING_SERVE_ENABLE_REMOTE_SERVICES"] == "false"
    assert environment["DOCLING_SERVE_MAX_FILE_SIZE"] == "2097152"
    assert environment["HF_HUB_OFFLINE"] == "1"
    assert not any(
        marker in key.upper()
        for key in environment
        for marker in ("DATABASE", "POSTGRES", "REDIS", "PASSWORD", "TOKEN")
    )


def test_synthetic_smoke_refuses_application_database(monkeypatch):
    from types import SimpleNamespace

    from scripts import docker_render_smoke

    monkeypatch.setattr(docker_render_smoke, "get_settings", lambda: SimpleNamespace(app_env="development"))
    monkeypatch.setenv("FORGE_TEST_PROJECT", "oju-video-forge")
    with pytest.raises(RuntimeError, match="isolated test environment"):
        docker_render_smoke.run("http://api:8000", False, False)
