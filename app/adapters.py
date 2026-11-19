"""Dual-role AI inference adapters for OJU Video Forge.

This module provides a shared Ollama transport and explicit vision/text roles.
Training text generation must use TRAINING_TEXT_* configuration and must not
fall back to the vision/general agent model.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .agents import AgentUnavailableError, LocalOllamaAdapter
from .config import get_settings
from .training import TrainingGeneration

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def canonical_ollama_endpoint(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return (
        parsed.scheme == "http"
        and parsed.hostname == "ollama"
        and (parsed.port is None or parsed.port == 11434)
        and parsed.path in {"", "/"}
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def endpoint_sha256(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RoleConfig:
    role: str
    provider: str
    url: str
    model: str
    model_digest: str
    stage_timeout_seconds: int
    generation_timeout_seconds: int

    @property
    def configured(self) -> bool:
        return bool(self.provider or self.url or self.model or self.model_digest)

    @property
    def complete(self) -> bool:
        return all((self.provider, self.url, self.model, self.model_digest))

    def validate(self, *, allow_host_endpoint: bool) -> None:
        if self.provider != "ollama":
            raise AgentUnavailableError(f"{self.role}_provider_unconfigured")
        if not self.model or not _DIGEST_RE.fullmatch(self.model_digest or ""):
            raise AgentUnavailableError(f"{self.role}_configuration_invalid")
        if canonical_ollama_endpoint(self.url):
            return
        if not allow_host_endpoint:
            raise AgentUnavailableError(f"{self.role}_endpoint_not_canonical")
        try:
            parsed = urlparse(self.url)
            host = parsed.hostname or ""
            try:
                local = ipaddress.ip_address(host).is_loopback
            except ValueError:
                local = host in {"localhost", "host.docker.internal"}
            if not (
                parsed.scheme == "http"
                and local
                and parsed.port is not None
                and 1 <= parsed.port <= 65535
                and parsed.path in {"", "/"}
                and not parsed.query
                and not parsed.fragment
                and not parsed.username
                and not parsed.password
            ):
                raise ValueError
        except (ValueError, TypeError):
            raise AgentUnavailableError(f"{self.role}_endpoint_not_canonical") from None

    def provenance(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "provider": self.provider,
            "model": self.model,
            "model_digest": self.model_digest,
            "endpoint_sha256": endpoint_sha256(self.url),
        }


def vision_role_config() -> RoleConfig:
    settings = get_settings()
    return RoleConfig(
        role="vision",
        provider=settings.agent_provider,
        url=settings.agent_url,
        model=settings.agent_model,
        model_digest=(settings.agent_model_digest or "").strip().lower(),
        stage_timeout_seconds=settings.agent_stage_timeout_seconds,
        generation_timeout_seconds=settings.agent_generation_timeout_seconds,
    )


def training_text_role_config() -> RoleConfig:
    settings = get_settings()
    return RoleConfig(
        role="training_text",
        provider=settings.training_text_provider,
        url=settings.training_text_url,
        model=settings.training_text_model,
        model_digest=(settings.training_text_model_digest or "").strip().lower(),
        stage_timeout_seconds=settings.training_text_stage_timeout_seconds,
        generation_timeout_seconds=settings.training_text_generation_timeout_seconds,
    )


class SharedOllamaTransport(LocalOllamaAdapter):
    """Shared, bounded Ollama transport for role-specific adapters."""

    role = "agent"

    def provenance(self) -> dict[str, Any]:
        data = super().provenance()
        data["role"] = self.role
        return data


class VisionInferenceAdapter(SharedOllamaTransport):
    role = "vision"


class TrainingTextAdapter(SharedOllamaTransport):
    role = "training_text"

    def generate_training(
        self, evidence: dict, check: Callable[[], None]
    ) -> TrainingGeneration:
        return super().generate_training(evidence, check)


def _build_adapter(config: RoleConfig) -> SharedOllamaTransport:
    settings = get_settings()
    allow_host_endpoint = settings.app_env in {"development", "test"}
    config.validate(allow_host_endpoint=allow_host_endpoint)
    adapter_cls = VisionInferenceAdapter if config.role == "vision" else TrainingTextAdapter
    return adapter_cls(
        config.url,
        config.model,
        config.model_digest,
        config.stage_timeout_seconds,
        config.generation_timeout_seconds,
        allow_host_endpoint=allow_host_endpoint,
    )


def get_vision_adapter() -> VisionInferenceAdapter:
    settings = get_settings()
    if not settings.agents_enabled:
        raise AgentUnavailableError("vision_adapter_disabled")
    adapter = _build_adapter(vision_role_config())
    assert isinstance(adapter, VisionInferenceAdapter)
    return adapter


def get_training_text_adapter() -> TrainingTextAdapter:
    settings = get_settings()
    if not settings.agents_enabled:
        raise AgentUnavailableError("training_text_disabled")
    config = training_text_role_config()
    if not config.complete:
        raise AgentUnavailableError("training_text_unconfigured")
    adapter = _build_adapter(config)
    assert isinstance(adapter, TrainingTextAdapter)
    return adapter


def training_text_status(existing_provider: dict | None = None) -> dict[str, Any]:
    settings = get_settings()
    if not settings.agents_enabled:
        return {"status": "disabled", "reason": "training_text_disabled", "provider": None}
    config = training_text_role_config()
    if not config.complete:
        return {
            "status": "unconfigured",
            "reason": "training_text_unconfigured",
            "provider": None,
            "role": "training_text",
        }
    # Config present but no runtime validation requested/provided: do not claim ready.
    if existing_provider is None:
        return {
            "status": "configured",
            "reason": "training_text_configured_unchecked",
            "provider": config.provenance(),
            "role": "training_text",
            "runtime_validated": False,
        }
    try:
        adapter = get_training_text_adapter()
    except AgentUnavailableError as exc:
        reason = str(exc)
        status = "unavailable" if "unavailable" in reason else "unconfigured"
        return {
            "status": status,
            "reason": reason,
            "provider": config.provenance(),
            "role": "training_text",
            "runtime_validated": False,
        }
    try:
        result = adapter.status(existing_provider)
    except AgentUnavailableError as exc:
        return {
            "status": "unavailable",
            "reason": str(exc),
            "provider": config.provenance(),
            "role": "training_text",
            "runtime_validated": False,
        }
    result["role"] = "training_text"
    result["runtime_validated"] = result.get("status") == "ready"
    return result
