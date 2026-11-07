from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
import yaml

from scripts import validate_acceptance_register, validate_repository


ROOT = Path(__file__).resolve().parents[1]


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
