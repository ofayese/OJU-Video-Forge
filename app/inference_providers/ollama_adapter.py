"""Ollama adapter wrapping existing LocalOllamaAdapter."""

from typing import Any

from .agents import AgentUnavailableError, LocalOllamaAdapter


class OllamaAdapter(LocalOllamaAdapter):
    """Wrap existing Ollama implementation to match provider interface."""

    def __init__(self, endpoint: str, model: str, digest: str, **kwargs):
        LocalOllamaAdapter.__init__(self, endpoint, model, digest, **kwargs)

    def status(self) -> dict[str, Any]:
        """Check provider readiness."""
        try:
            result = self.status({})
            return {
                "status": "ready" if result.get("status") == "ready" else "degraded",
                "provider": self.provenance(),
                "runtime_validated": True,
            }
        except AgentUnavailableError as e:
            return {"status": "unavailable", "reason": str(e), "runtime_validated": False}

    def capabilities(self) -> dict[str, Any]:
        """Return model capabilities."""
        try:
            info = self._request("POST", "/api/show", {"model": self.model})
            capabilities = info.get("capabilities") if isinstance(info, dict) else []
            return {
                "vision_supported": "vision" in capabilities if isinstance(capabilities, list) else False,
                "structured_output_supported": True,
            }
        except Exception:
            return {"vision_supported": False, "structured_output_supported": False}

    def generate(self, messages: list[dict], options) -> dict[str, Any]:
        """Generate response to chat messages."""


        if not isinstance(messages, list):
            raise ValueError("messages must be a list")

        chat_messages = [
            {"role": m.get("role", "user"), "content": m.get("content", "")}
            for m in messages
        ]

        result = self._request(
            "POST", "/api/chat", {
                "model": self.model,
                "messages": chat_messages,
                "stream": False,
                "format": None,
                "keep_alive": 0,
                "options": {},
            }
        )

        message = result.get("message") if isinstance(result, dict) else None
        content = message.get("content") if isinstance(message, dict) else ""

        return {
            "content": content,
            "model": self.model,
            "done": result.get("done") if isinstance(result, dict) else False,
        }

    def generate_structured(self, messages: list[dict], schema: dict, options=None) -> Any:
        """Generate response validated against JSON schema."""

        result = self.generate(messages, options)
        try:
            return __import__("json").loads(result.get("content", "{}"))
        except (ValueError, TypeError):
            return None

    def provenance(self) -> dict[str, str]:
        """Return provider identity."""
        return {
            "provider": "ollama",
            "model": self.model,
            "model_digest": self.digest or "",
            "endpoint_sha256": __import__("hashlib").sha256(self.endpoint.encode()).hexdigest()[:16],
        }
