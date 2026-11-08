#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
docker build --target test -t oju-video-forge-tests:local -f services/api/Dockerfile .
docker run --rm --network none oju-video-forge-tests:local ruff check --no-cache app alembic tests scripts
docker run --rm --network none oju-video-forge-tests:local python -m pytest -q -p no:cacheprovider -rs
docker compose --env-file .env.development.example -f compose.yaml config --quiet
printf 'Static checks passed. This is NOT proof of successful GPU or integrated video generation.\n'
