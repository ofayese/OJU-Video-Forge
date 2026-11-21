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
    assert environment["DOCLING_ARTIFACTS_PATH"] == "/opt/app-root/src/.cache/docling/models"
    assert environment["HF_HUB_OFFLINE"] == "1"
    assert environment["HOME"] == "/tmp"
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


def test_no_undefined_network_references():
    """Every service network reference must match a defined network."""
    base = yaml.safe_load((ROOT / "compose.yaml").read_text())
    defined_networks = set(base.get("networks", {}).keys())
    for svc_name, svc in base["services"].items():
        for net in svc.get("networks", []):
            assert net in defined_networks, (
                f"Service {svc_name} references undefined network {net}"
            )
    # Same check for each overlay
    for env_file in ("compose.acceptance.yaml", "compose.integration.yaml", "compose.narration.yaml", "compose.ollama.yaml"):
        overlay_path = ROOT / env_file
        if not overlay_path.exists():
            continue
        overlay = yaml.load(overlay_path.read_text(), Loader=ComposeLoader)
        overlay_networks = set(overlay.get("networks", {}).keys())
        for svc_name, svc in overlay.get("services", {}).items():
            for net in svc.get("networks", []):
                assert net in defined_networks or net in overlay_networks, (
                    f"{env_file}: Service {svc_name} references undefined network {net}"
                )


def test_no_unintended_service_dependencies():
    """Overlay services must not add depends_on to infrastructure services."""
    for env_file in ("compose.acceptance.yaml", "compose.integration.yaml"):
        overlay = yaml.load((ROOT / env_file).read_text(), Loader=ComposeLoader)
        for svc_name in ("postgres", "redis", "ollama", "comfyui", "docling-ocr"):
            svc = overlay.get("services", {}).get(svc_name, {})
            assert "depends_on" not in svc, (
                f"{env_file}: {svc_name} should not have depends_on in overlay"
            )
        # Frontend should not depend on backend services in test overlays
        frontend = overlay.get("services", {}).get("frontend", {})
        assert "depends_on" not in frontend, (
            f"{env_file}: frontend should not have depends_on in overlay"
        )


def test_overlay_volumes_and_networks_are_isolated():
    """Overlay volumes and networks must use environment-specific names."""
    for env, prefix in (("acceptance", "oju-video-forge-acceptance"), ("integration", "oju-video-forge-integration")):
        overlay = yaml.load((ROOT / f"compose.{env}.yaml").read_text(), Loader=ComposeLoader)
        assert overlay["name"] == prefix
        for vol_name, vol_def in overlay.get("volumes", {}).items():
            assert vol_def.get("name", "").startswith(prefix), (
                f"compose.{env}.yaml: volume {vol_name} not isolated with prefix {prefix}"
            )
        for net_name, net_def in overlay.get("networks", {}).items():
            assert net_def.get("name", "").startswith(prefix), (
                f"compose.{env}.yaml: network {net_name} not isolated with prefix {prefix}"
            )


def test_privileged_boundary_services_keep_no_new_privileges():
    """Every service that drops privileges at runtime must keep no-new-privileges.

    ``x-common`` sets ``security_opt: ["no-new-privileges:true"]``. A service that
    needs extra capabilities (the entrypoint uses gosu, which requires SETUID and
    SETGID for the root -> forge drop) used to override ``security_opt: []`` to add
    them back. That also silently removed no-new-privileges at runtime, contradicting
    docs/image-provenance-register.md, while ``setuid`` is exactly the case the option
    exists to constrain.

    ``TestEnvironmentsHave...`` only asserted this for docling-ocr and comfyui, so the
    drift on api/render-worker/migrate went unnoticed. Adding a capability must not
    require removing the security option.
    """
    base = yaml.safe_load((ROOT / "compose.yaml").read_text())
    privileged = {
        name
        for name, service in base["services"].items()
        if service.get("cap_add")
    }
    # The compose file must actually exercise this rule, not pass vacuously.
    assert {"api", "render-worker"} <= privileged

    for name in ("api", "render-worker", "dispatcher", "frontend", "migrate"):
        security_opt = base["services"][name].get("security_opt")
        # ``None`` means the service inherits the x-common anchor; an explicit list
        # overrides it entirely, so an empty list would silently drop the option.
        if security_opt is None:
            assert base["x-common"]["security_opt"] == ["no-new-privileges:true"], name
        else:
            assert "no-new-privileges:true" in security_opt, (
                f"{name} overrides security_opt without no-new-privileges: {security_opt}"
            )

    # Capability additions are limited to the privilege-drop pair.
    for name in ("api", "render-worker"):
        assert set(base["services"][name]["cap_add"]) == {"SETUID", "SETGID"}
