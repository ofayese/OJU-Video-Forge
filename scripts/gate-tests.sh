#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python -m compileall -q app alembic tests
python -m pytest -q tests
docker compose config --quiet
printf 'Static checks passed. This is NOT proof of successful GPU or integrated video generation.\n'
