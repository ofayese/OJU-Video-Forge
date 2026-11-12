from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts import validate_acceptance_register, validate_repository

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("bash") is None, reason="Bash is required to exercise the lifecycle helper")
@pytest.mark.parametrize(("environment", "narration", "expected"), [
    ("application", False, 0),
    ("application", True, 0),
    ("acceptance", True, 1),
])
def test_forge_bash_selects_narration_overlay(tmp_path, environment, narration, expected):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(ROOT / "scripts" / "forge.sh", scripts / "forge.sh")
    (tmp_path / ".env").write_text(f"DB_PASSWORD={'x' * 40}\n", encoding="utf-8")
    docker = tmp_path / "docker"
    docker.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n', encoding="utf-8")
    docker.chmod(0o755)
    env = os.environ.copy()
    for key in ("COMPOSE_PROJECT_NAME", "COMPOSE_FILE", "COMPOSE_PROFILES", "DB_PASSWORD"):
        env.pop(key, None)
    env["PATH"] = f"{tmp_path}{os.pathsep}{env['PATH']}"
    args = ["bash", str(scripts / "forge.sh"), environment]
    if narration:
        args.append("--narration")
    result = subprocess.run([*args, "config", "--quiet"], env=env, capture_output=True, text=True)
    assert result.returncode == expected
    if expected == 0:
        assert ("compose.narration.yaml" in result.stdout) is narration
        assert result.stdout.splitlines()[1:3] == ["--project-name", "oju-video-forge"]
    else:
        assert "only for the application environment" in result.stderr


def test_customization_validation_rejects_list_apply_to(tmp_path, monkeypatch):
    github = tmp_path / ".github"
    instructions = github / "instructions"
    instructions.mkdir(parents=True)
    (github / "agents").mkdir()
    (instructions / "example.instructions.md").write_text(
        '---\nname: Example\ndescription: Example instruction\napplyTo:\n  - "**/*.py"\n---\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(validate_repository, "GITHUB", github)

    with pytest.raises(ValueError, match="applyTo must scope the instruction"):
        validate_repository.validate_customizations()


def test_customization_validation_accepts_repository_frontmatter(tmp_path, monkeypatch):
    github = tmp_path / ".github"
    shutil.copytree(ROOT / ".github" / "instructions", github / "instructions")
    (github / "agents").mkdir()
    monkeypatch.setattr(validate_repository, "GITHUB", github)

    validate_repository.validate_customizations()


@pytest.mark.parametrize(
    "register",
    [
        {"current_gates": None},
        {"current_gates": [{"gate_id": ["invalid"], "status": "NOT_RUN"}]},
    ],
)
def test_acceptance_validation_returns_schema_errors_for_invalid_gate_types(tmp_path, register):
    path = tmp_path / "register.yaml"
    path.write_text(yaml.safe_dump(register), encoding="utf-8")

    assert validate_acceptance_register.validate(path)


def test_acceptance_validation_accepts_relative_register_path(monkeypatch, capsys):
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(
        sys,
        "argv",
        ["validate_acceptance_register.py", "--register", "docs/reviews/ERE-ACCEPTANCE-REGISTER.yaml"],
    )

    assert validate_acceptance_register.main() == 0
    assert "PASS: docs/reviews/ERE-ACCEPTANCE-REGISTER.yaml matches" in capsys.readouterr().out


def test_action_pin_validation_matches_nested_list_actions(tmp_path, monkeypatch):
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    sha = "a" * 40
    workflow = workflows / "ci.yml"
    workflow.write_text(
        f"steps:\n  - uses: github/codeql-action/init@{sha}\n", encoding="utf-8"
    )
    monkeypatch.setattr(validate_repository, "GITHUB", tmp_path)

    validate_repository.validate_action_pins()

    workflow.write_text(
        "steps:\n  - uses: github/codeql-action/init@main\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="not pinned to a commit SHA"):
        validate_repository.validate_action_pins()
