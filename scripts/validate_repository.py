from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from unittest.mock import patch
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
GITHUB = ROOT / ".github"
ENV_FILES = (
    ROOT / ".env.example",
    ROOT / ".env.development.example",
    ROOT / ".env.production.example",
)
COMPOSE_FILES = tuple(ROOT.glob("compose*.yaml"))
ACTION_USE = re.compile(r"^\s*(?:-\s+)?uses:\s+([\w.-]+/[\w./-]+)@([^\s#]+)", re.MULTILINE)
SHA = re.compile(r"^[0-9a-f]{40}$")


def _read_frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path.relative_to(ROOT)}: missing YAML frontmatter")
    _, raw, _ = text.split("---", 2)
    metadata = yaml.safe_load(raw)
    if not isinstance(metadata, dict):
        raise ValueError(f"{path.relative_to(ROOT)}: frontmatter must be a mapping")
    return metadata


def _env_keys(path: Path) -> set[str]:
    return {
        match.group(1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if (match := re.match(r"^\s*([A-Z][A-Z0-9_]*)\s*=", line))
    }


def validate_yaml() -> None:
    paths = [
        *GITHUB.glob("workflows/*.yml"),
        *GITHUB.glob("ISSUE_TEMPLATE/*.yml"),
        GITHUB / "dependabot.yml",
        ROOT / "docs/reviews/ERE-ACCEPTANCE-REGISTER.yaml",
    ]
    for path in paths:
        yaml.safe_load(path.read_text(encoding="utf-8"))


def validate_customizations() -> None:
    for path in (GITHUB / "instructions").glob("*.instructions.md"):
        metadata = _read_frontmatter(path)
        if not isinstance(metadata.get("description"), str) or not metadata["description"].strip():
            raise ValueError(f"{path.name}: description is required")
        apply_to = metadata.get("applyTo")
        if not isinstance(apply_to, (str, list)) or not apply_to:
            raise ValueError(f"{path.name}: applyTo must scope the instruction")
        if apply_to == "**" or apply_to == ["**"]:
            raise ValueError(f"{path.name}: broad applyTo scope is not allowed")

    for path in (GITHUB / "agents").glob("*.agent.md"):
        metadata = _read_frontmatter(path)
        if not isinstance(metadata.get("description"), str) or not metadata["description"].strip():
            raise ValueError(f"{path.name}: description is required")
        if not isinstance(metadata.get("tools"), list):
            raise ValueError(f"{path.name}: tools must be an explicit list")


def validate_action_pins() -> None:
    for path in (GITHUB / "workflows").glob("*.yml"):
        for repository, reference in ACTION_USE.findall(path.read_text(encoding="utf-8")):
            if not SHA.fullmatch(reference):
                raise ValueError(
                    f"{path.name}: action {repository}@{reference} is not pinned to a commit SHA"
                )


def validate_environment_templates() -> None:
    template_sets = [_env_keys(path) for path in ENV_FILES]
    if any(keys != template_sets[0] for keys in template_sets[1:]):
        raise ValueError("Root environment templates must expose the same keys")

    compose_variables: set[str] = set()
    for path in COMPOSE_FILES:
        compose_variables.update(
            re.findall(r"\$\{([A-Z][A-Z0-9_]*)", path.read_text(encoding="utf-8"))
        )
    missing = compose_variables - template_sets[0]
    if missing:
        raise ValueError("Compose variables missing from root environment templates: " + ", ".join(sorted(missing)))

    from app.config import Settings

    values = {}
    for line in ENV_FILES[1].read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*)$", line)
        if match and not line.lstrip().startswith("#"):
            values[match.group(1)] = match.group(2)
    with patch.dict(os.environ, values, clear=True):
        Settings.load()


def main() -> int:
    try:
        validate_yaml()
        validate_customizations()
        validate_action_pins()
        validate_environment_templates()
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("PASS: YAML, Copilot metadata, action pins, and environment templates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
