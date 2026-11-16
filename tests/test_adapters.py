import pytest

from app import adapters, agents
from app.config import Settings


def test_training_text_role_config_is_separate_and_fail_closed(monkeypatch):
    monkeypatch.setattr(adapters, "get_settings", lambda: Settings(
        agents_enabled=True,
        agent_provider="ollama",
        agent_url="http://ollama:11434",
        agent_model="qwen3-vl:4b",
        agent_model_digest="a" * 64,
        training_text_provider="ollama",
        training_text_url="http://ollama:11434",
        training_text_model="",
        training_text_model_digest="",
    ))
    cfg = adapters.training_text_role_config()
    assert cfg.role == "training_text"
    assert cfg.url == "http://ollama:11434"
    assert cfg.model == ""
    assert not cfg.complete
    with pytest.raises(agents.AgentUnavailable, match="training_text_unconfigured"):
        adapters.get_training_text_adapter()


def test_training_text_does_not_fallback_to_agent_model(monkeypatch):
    monkeypatch.setattr(adapters, "get_settings", lambda: Settings(
        agents_enabled=True,
        agent_provider="ollama",
        agent_url="http://ollama:11434",
        agent_model="qwen3-vl:4b",
        agent_model_digest="a" * 64,
        training_text_provider="",
        training_text_url="",
        training_text_model="",
        training_text_model_digest="",
    ))
    with pytest.raises(agents.AgentUnavailable, match="training_text_unconfigured"):
        adapters.get_training_text_adapter()


def test_training_text_status_explicit_when_unconfigured(monkeypatch):
    monkeypatch.setattr(adapters, "get_settings", lambda: Settings(
        agents_enabled=True,
        training_text_provider="ollama",
        training_text_url="http://ollama:11434",
        training_text_model="",
        training_text_model_digest="",
    ))
    status = adapters.training_text_status()
    assert status["status"] == "unconfigured"
    assert status["reason"] == "training_text_unconfigured"
    assert status.get("role") == "training_text"


def test_training_text_disabled_without_blocking_vision_config(monkeypatch):
    monkeypatch.setattr(adapters, "get_settings", lambda: Settings(
        agents_enabled=False,
        agent_provider="ollama",
        agent_url="http://ollama:11434",
        agent_model="qwen3-vl:4b",
        agent_model_digest="a" * 64,
        training_text_provider="ollama",
        training_text_url="http://ollama:11434",
        training_text_model="writer:1",
        training_text_model_digest="b" * 64,
    ))
    assert adapters.training_text_status()["status"] == "disabled"
    with pytest.raises(agents.AgentUnavailable, match="vision_adapter_disabled"):
        adapters.get_vision_adapter()


def test_training_text_rejects_non_canonical_endpoint_in_production(monkeypatch):
    monkeypatch.setattr(adapters, "get_settings", lambda: Settings(
        app_env="production",
        agents_enabled=True,
        training_text_provider="ollama",
        training_text_url="http://localhost:11434",
        training_text_model="writer:1",
        training_text_model_digest="b" * 64,
    ))
    with pytest.raises(agents.AgentUnavailable, match="training_text_endpoint_not_canonical"):
        adapters.get_training_text_adapter()


def test_training_text_configured_but_unchecked_not_ready(monkeypatch):
    monkeypatch.setattr(adapters, "get_settings", lambda: Settings(
        agents_enabled=True,
        training_text_provider="ollama",
        training_text_url="http://ollama:11434",
        training_text_model="writer:1",
        training_text_model_digest="b" * 64,
    ))
    status = adapters.training_text_status()
    assert status["status"] == "configured"
    assert status.get("reason") == "training_text_configured_unchecked"
    assert status.get("runtime_validated") is False
    assert status.get("role") == "training_text"


def test_generic_agent_adapter_not_used_for_training_text(monkeypatch):
    called = {"agent": 0}

    def _boom(*args, **kwargs):
        called["agent"] += 1
        raise AssertionError("generic agent adapter must not be used")

    monkeypatch.setattr(agents, "get_agent_adapter", _boom)
    monkeypatch.setattr(adapters, "get_settings", lambda: Settings(
        agents_enabled=True,
        agent_provider="ollama",
        agent_url="http://ollama:11434",
        agent_model="qwen3-vl:4b",
        agent_model_digest="a" * 64,
        training_text_provider="",
        training_text_url="",
        training_text_model="",
        training_text_model_digest="",
    ))
    with pytest.raises(agents.AgentUnavailable, match="training_text_unconfigured"):
        adapters.get_training_text_adapter()
    assert called["agent"] == 0
