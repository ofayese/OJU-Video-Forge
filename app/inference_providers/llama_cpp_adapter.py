"""llama.cpp adapter implementation."""

import json
from typing import Any

from .__init__ import AgentUnavailableError, InferenceProvider


class LlamaCppAdapter(InferenceProvider):
    """Adapter for llama.cpp server API (/v1/chat/completions)."""

    def __init__(self, url: str = "http://llama-cpp:8080", model: str = "", digest: str = ""):
        self.url = url.rstrip("/")
        self.model = model or "mistral-latest"
        self.digest = digest
        self._client = __import__("httpx").Client(timeout=30.0)

    def status(self) -> dict[str, Any]:
        """Check provider readiness and get model info."""
        try:
            response = self._client.get(f"{self.url}/v1/models", raise_for_status=False)

            if response.status_code != 200:
                return {
                    "status": "unavailable",
                    "reason": f"Model list unavailable: {response.status_code}",
                    "runtime_validated": False,
                }

            models = response.json().get("data", [])
            if not models or not isinstance(models, list):
                return {"status": "degraded", "runtime_validated": False}

            found_model = None
            for m in models:
                if isinstance(m, dict) and m.get("name") == self.model:
                    found_model = m
                    break

            if not found_model:
                return {
                    "status": "degraded",
                    "reason": f"Model {self.model} not found",
                    "runtime_validated": False,
                }

            return {
                "status": "ready",
                "provider": self.provenance(),
                "runtime_validated": True,
            }
        except Exception as e:
            return {"status": "unavailable", "reason": str(e), "runtime_validated": False}

    def capabilities(self) -> dict[str, Any]:
        """Return model capabilities."""
        try:
            response = self._client.get(f"{self.url}/v1/models", timeout=5.0)
            if response.status_code == 200:
                models = response.json().get("data", [])

                has_vision = any(
                    isinstance(m, dict) and
                    "vision" in m.get("details", {}).get("general", "")
                    for m in models
                ) if models else False

                return {
                    "vision_supported": has_vision or False,
                    "structured_output_supported": True,
                }
        except Exception:
            pass

        return {"vision_supported": False, "structured_output_supported": False}

    def generate(self, messages: list[dict], options) -> dict[str, Any]:
        """Generate completion."""
        try:
            stop = getattr(options, 'stop_sequences', []) or []

            payload = {
                "model": self.model,
                "messages": [
                    {"role": m.get("role", "user"), "content": m.get("content", "")}
                    for m in messages
                ],
                "stream": False,
                "stop": stop,
            }

            response = self._client.post(
                f"{self.url}/v1/chat/completions",
                json=payload,
                raise_for_status=True,
            )

            data = response.json()

            if data.get("error"):
                raise AgentUnavailableError(data["error"])

            content = data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""

            return {
                "content": content,
                "model": self.model,
                "done": True,
                "finish_reason": data.get("choices", [{}])[0].get("finish_reason"),
            }
        except json.JSONDecodeError as e:
            raise AgentUnavailableError("Invalid JSON response") from e
        except __import__("httpx").HTTPStatusError as e:
            raise AgentUnavailableError(f"HTTP error: {e.response.status_code}") from e
        except Exception as e:
            raise AgentUnavailableError(str(e)) from e

    def generate_structured(self, messages: list[dict], schema: dict, options=None) -> Any:
        """Generate structured output."""
        try:
            result = self.generate(messages, options)

            if not result.get("content"):
                return None

            try:
                return __import__("json").loads(result["content"])
            except (ValueError, TypeError):
                return None

        except AgentUnavailableError:
            return None

    def provenance(self) -> dict[str, str]:
        """Return provider identity."""
        return {
            "provider": "llama_cpp",
            "model": self.model or "",
            "model_digest": self.digest or "",
            "endpoint_sha256": __import__("hashlib").sha256(self.url.encode()).hexdigest()[:16],
        }
