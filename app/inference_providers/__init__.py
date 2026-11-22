"""Provider-agnostic inference contract for OJU Video Forge."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class AgentUnavailableError(RuntimeError):
    """Raised when an inference provider cannot serve requests."""
    pass


@dataclass(frozen=True)
class ModelIdentity:
    """Model identity with full provenance."""
    name: str
    digest: str = None
    provider: str = ""


@dataclass(frozen=True)
class GenerationOptions:
    """Generation configuration for the model."""
    temperature: float = 0.0
    num_ctx: int = 4096
    num_predict: int = 3072
    stop_sequences: list = field(default_factory=list)


class InferenceProvider(ABC):
    """Provider-agnostic inference contract.

    All OJU Video Forge inference providers must implement this interface.
    The application layer handles orchestration, evidence attribution, training workflow,
    and human review - not provider-specific logic.
    """

    @abstractmethod
    def status(self) -> dict[str, Any]:
        """Return provider readiness and model verification."""
        pass

    @abstractmethod
    def capabilities(self) -> dict[str, Any]:
        """Return model capabilities (text, vision, structured output)."""
        pass

    @abstractmethod
    def generate(self, messages: list[dict], options: GenerationOptions) -> dict[str, Any]:
        """Generate response to chat messages."""
        pass

    @abstractmethod
    def generate_structured(
        self,
        messages: list[dict],
        schema: dict[str, Any],
        options: GenerationOptions | None = None,
    ) -> Any:
        """Generate response validated against JSON schema."""
        pass

    @abstractmethod
    def provenance(self) -> dict[str, str]:
        """Return provider identity information."""
        pass


def validate_inference_options(num_ctx: int = 4096, num_predict: int = 3072) -> None:
    """Validate generation options are within bounds."""
    if not 1024 <= num_ctx <= 8192:
        raise ValueError(f"num_ctx must be between 1024 and 8192, got {num_ctx}")
    if num_predict < 1 or num_predict > 8192:
        raise ValueError(f"num_predict must be between 1 and 8192, got {num_predict}")


def build_model_digest_sha256(model_name: str, endpoint_url: str) -> str:
    """Build identifier hash for model+endpoint combination."""
    payload = f"{model_name}:{endpoint_url}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
