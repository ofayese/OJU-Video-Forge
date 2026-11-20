# OJU Video Forge — Agent Onboarding

## What it is
Local-first, containerized platform for generating training videos from approved procedures. Backend: FastAPI + PostgreSQL + Redis + worker; frontend: Vite/React; rendering via FFmpeg; optional Ollama for narration.

## Current state (verify, don't assume)
- Branch: main. Verify: git rev-parse HEAD, git status --short, git log --oneline -10.
- Do NOT characterize implementation status from README/plans alone. Verify current source, tests, Git state, and runtime before asserting missing capabilities.
- Reported baseline: 477 passed, 16 skipped. Run: python -m pytest -q -p no:cacheprovider (needs Postgres/Redis per CI env).
- Ruff: uff check app tests alembic scripts. Compile: python -m compileall -q app tests alembic scripts.

## Implemented & verified (evidence in source/tests)
Auth (session tokens, project membership), PG records, Redis dispatch, FFmpeg render (explicit H.264/AAC profile), TrainingDraft + provenance, evidence/script review, final-video review, project-scoped review queue, branching-procedure schema validation, HMAC-signed ERE telemetry.

## Architecture decisions
- ADR-OVF-001, ADR-OVF-002: Accepted.
- ADR-OVF-003 (execution gateway): Proposed. Strict end-to-end inference exclusivity BLOCKED. Do not implement gateway.

## Environment
Docker Compose (compose.dev.yaml). Services: api (8000), frontend (5173), postgres, redis, render-worker, dispatcher, docling-ocr, ollama, comfyui, telemetry publisher. Canonical Ollama endpoint: http://ollama:11434.

## Key commands
- Lint: uff check app tests alembic scripts
- Tests: python -m pytest -q -p no:cacheprovider
- Compose: docker compose -f compose.dev.yaml config
- Frontend: cd frontend && npm run build && npm test

## Constraints
- No hardcoded user paths; relative compose contexts.
- No model downloads without approval.
- No persistent Windows service without approval.
- CI Trivy retains unfixed findings (no --ignore-unfixed).
- Never push/merge unless asked. Local commits only when validated.

## Tooling
Kilo config: ~/.config/kilo/kilo.jsonc. Core tools: file read/edit, bash, git, python/pytest, docker, docs. Disable unrelated providers only; keep core dev capabilities.

## Docs authority
Canonical: docs/reviews/ERE-ACCEPTANCE-REGISTER.yaml, docs/ere-development-baseline-register.md, docs/development/DEFINITION-OF-DONE.md. Historical plans (VERTICAL-SLICE-PLAN.md) are milestones, not current truth. Superseded docs must be marked.
