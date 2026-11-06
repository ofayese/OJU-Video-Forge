from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
REGISTER = ROOT / "docs" / "reviews" / "ERE-ACCEPTANCE-REGISTER.yaml"
SCHEMA = ROOT / "docs" / "reviews" / "acceptance-register.schema.json"


def validate(path: Path) -> list[str]:
    register: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = [
        f"{'/'.join(map(str, error.absolute_path)) or '<root>'}: {error.message}"
        for error in validator.iter_errors(register)
    ]

    if errors or not isinstance(register, dict):
        return errors

    gates = register.get("current_gates", [])
    identifiers = [gate.get("gate_id") for gate in gates if isinstance(gate, dict)]
    if len(identifiers) != len(set(identifiers)):
        errors.append("current_gates: gate_id values must be unique")

    for gate in gates:
        if not isinstance(gate, dict) or gate.get("status") != "PASS":
            continue
        gate_id = gate.get("gate_id", "<unknown>")
        required_for_pass = {
            "commit_sha": gate.get("commit_sha"),
            "test_command": gate.get("test_command"),
            "execution_environment": gate.get("execution_environment"),
            "validation_date": gate.get("validation_date"),
        }
        missing = [key for key, value in required_for_pass.items() if not value]
        if not gate.get("evidence_reference"):
            missing.append("evidence_reference")
        if gate.get("exit_code") != 0:
            missing.append("exit_code=0")
        if missing:
            errors.append(f"{gate_id}: PASS requires " + ", ".join(missing))

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the acceptance register.")
    parser.add_argument("--register", type=Path, default=REGISTER)
    args = parser.parse_args()
    errors = validate(args.register)
    if errors:
        print("\n".join(f"ERROR: {error}" for error in errors))
        return 1
    print(f"PASS: {args.register} matches the acceptance schema")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
