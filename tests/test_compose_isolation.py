from pathlib import Path

import yaml
import pytest


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


def test_synthetic_smoke_refuses_application_database(monkeypatch):
    from types import SimpleNamespace
    from scripts import docker_render_smoke

    monkeypatch.setattr(docker_render_smoke, "get_settings", lambda: SimpleNamespace(app_env="development"))
    monkeypatch.setenv("FORGE_TEST_PROJECT", "oju-video-forge")
    with pytest.raises(RuntimeError, match="isolated test environment"):
        docker_render_smoke.run("http://api:8000", False, False)
