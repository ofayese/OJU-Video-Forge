"""Manual operator probe: ask the OJU Ollama vision model for a structured draft.

This is NOT a pytest module. It has no tests and must never run as a side effect of
test collection, so all work happens inside ``main()`` behind a ``__main__`` guard.

Earlier this file executed ``docker exec ... ollama run`` at import time. Because
``pytest.ini`` now scopes collection to ``tests/`` and the top-level name still
matches pytest's default ``test_*.py`` pattern, the guard below is a second line of
defence: importing this file is always side-effect free.

The child process emits UTF-8; decoding is pinned explicitly so a Windows ANSI code
page can never raise ``UnicodeDecodeError`` in the subprocess reader thread.
"""

from __future__ import annotations

import json
import subprocess

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "audience": {"type": "string"},
        "learning_objectives": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "kind": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "kind", "evidence"],
            },
        },
        "chapters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "start_ms": {"type": "integer"},
                    "end_ms": {"type": "integer"},
                    "text": {"type": "string"},
                    "kind": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "start_ms", "end_ms", "text", "kind", "evidence"],
            },
        },
        "script": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "start_ms": {"type": "integer"},
                    "end_ms": {"type": "integer"},
                    "text": {"type": "string"},
                    "kind": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "start_ms", "end_ms", "text", "kind", "evidence"],
            },
        },
    },
    "required": ["title", "audience", "learning_objectives", "chapters", "script"],
}

PROMPT = (
    "Create a minimal training draft for a 5 second video showing a blue screen. "
    "Evidence: frame-0. Return only the JSON."
)


def main() -> int:
    result = subprocess.run(
        [
            "docker", "exec", "oju-video-forge-ollama-1", "ollama", "run", "qwen3-vl:4b",
            "--format", json.dumps(SCHEMA),
            PROMPT,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    print("STDOUT:", result.stdout)
    print("STDERR:", result.stderr)
    print("Return code:", result.returncode)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
