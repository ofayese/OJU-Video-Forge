from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from openapi_spec_validator import validate_spec

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and validate the API schema.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    from app.api import app

    schema = app.openapi()
    validate_spec(schema)
    encoded = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
