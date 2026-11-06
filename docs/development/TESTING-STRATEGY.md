# Testing strategy

## Layers

- **Unit/contract:** Pure Python and TypeScript behavior, validation, authorization
  responses, API/OpenAPI contracts, and frontend client behavior. No external
  services, public endpoints, or GPU.
- **Database/queue integration:** GitHub-hosted CI service containers only. Use
  isolated PostgreSQL and Redis instances with synthetic fixtures; never connect
  CI or local tests to shared ERE databases.
- **Rendering:** Verify timeline validation and subprocess restrictions without
  requiring FFmpeg. A separate CPU-only integration check installs FFmpeg/FFprobe
  and validates a synthetic MP4, metadata, size, and SHA-256.
- **GPU/inference:** Separate, opt-in local ERE acceptance. Do not require GPU on
  hosted CI. A profile/configuration check is not GPU validation.
- **End-to-end/recovery:** Explicitly exercise account/project ownership, upload,
  persistent job dispatch, worker recovery, artifact access, and failure paths
  before claiming the vertical slice complete.

## Commands

Backend: `python -m pip check`, `python -m pip-audit -r requirements.lock.txt`,
`ruff check app tests alembic scripts`, `python -m compileall -q app tests alembic`,
and `python -m pytest -q`.

Frontend: `npm ci`, `npm audit`, `npm run typecheck`, `npm test`, and `npm run build`
from `frontend/`. The package test script uses `vitest run` and is non-interactive.

Infrastructure: validate Compose configuration without starting services, check
Dockerfile syntax, validate YAML/instructions/acceptance schemas, and generate and
validate OpenAPI. See CI workflows for the authoritative PR commands.

## Evidence

Record command, exit code, environment/runtime, test counts, skips, warnings, source
commit, date, and unverified gates. Keep raw logs, credentials, private media, and
internal service inventories out of the public repository. See
[evidence classification](../governance/EVIDENCE-CLASSIFICATION.md).
