# Docker-first development workflow

Development and application tests run in Docker Linux containers on ERE. Docker
Desktop/WSL2, Git, and the editor are host infrastructure; do not install application
Python, npm, FFmpeg, PostgreSQL, or Redis dependencies into Windows.
Cloud and GPU providers are optional. Both ADRs remain PROPOSED.

## Environment and startup

Use the [Docker environment runbook](../operations/DOCKER-ENVIRONMENTS.md) for
canonical naming, independent secrets/volumes/networks, port allocation, backup,
owner-approved reconciliation, and safe shutdown.

The canonical application is `oju-video-forge`; acceptance and integration are
`oju-video-forge-acceptance` and `oju-video-forge-integration`. Do not reuse writable
storage or database secrets across them. Check `COMPOSE_PROJECT_NAME`/`COMPOSE_FILE`
and existing listeners before starting anything. Never migrate or remove existing
data without owner approval.

```powershell
.\scripts\forge.ps1 -Environment application -Plan
.\scripts\forge.ps1 -Environment application build
.\scripts\forge.ps1 -Environment application up -d --wait
.\scripts\forge.ps1 -Environment application ps
.\scripts\forge.ps1 -Environment application stop
```

Private environment files must exist and have unique URL-safe random database
secrets before executing these commands. The `migrate` one-shot job must succeed
before API, dispatcher, and worker startup. For Vite development, explicitly add
`compose.dev.yaml` to the canonical base configuration; it publishes the API on
loopback port 8000 and mounts only frontend source/index files. Do not combine it
with test overlays.

## Container-only checks

```powershell
docker build --target test -t oju-video-forge-tests:local -f services\api\Dockerfile .
docker run --rm --network none oju-video-forge-tests:local python -m pip check
docker run --rm --network none oju-video-forge-tests:local ruff check --no-cache app tests alembic scripts
docker run --rm --network none oju-video-forge-tests:local python -m pytest -q -p no:cacheprovider -rs
docker run --rm oju-video-forge-tests:local pip-audit -r requirements.lock.txt
docker run --rm oju-video-forge-tests:local pip-audit -r requirements-dev.lock.txt
docker run --rm --network none oju-video-forge-tests:local python scripts/validate_repository.py
docker run --rm --network none oju-video-forge-tests:local python scripts/validate_acceptance_register.py
docker run --rm --network none oju-video-forge-tests:local python scripts/export_openapi.py --output /tmp/openapi.json
docker build --target test -t oju-video-forge-frontend-tests:local frontend
docker compose --env-file .env.development.example -f compose.yaml -f compose.dev.yaml config --quiet
docker compose --env-file .env.development.example -f compose.yaml -f compose.acceptance.yaml config --quiet
docker compose --env-file .env.development.example -f compose.yaml -f compose.integration.yaml config --quiet
```

The frontend test image runs lockfile-based `npm ci`, type checking, Vitest,
production build, and npm audit. Tests that require disposable PostgreSQL/Redis
remain explicitly skipped in the offline backend suite; a unit-test pass is not
an integrated job-processing pass.

Bash uses the same Docker commands with shell path separators; run
`bash scripts/gate-tests.sh` for offline backend/configuration checks and
`bash scripts/forge.sh application ...` for environment-specific operations.

For real synthetic rendering, provision the approved isolated acceptance project,
then run `.\scripts\acceptance.ps1`. Its preflight checks Docker labels, volume
ownership namespaces, networks, and writable bind mounts before creating data.
Retain test volumes until their disposition is explicitly approved.

If dependency inputs change, regenerate both Python locks with pinned `pip-tools`
inside the test/tool container, using `--generate-hashes --allow-unsafe`; export
the resulting locks through a narrowly scoped mount. See the
[dependency update policy](./DEPENDENCY-UPDATE-POLICY.md). Do not regenerate locks
with unpinned host tooling.

## Change workflow

1. Start from verified current `main` on a focused branch; preserve local changes.
2. Link work to issue acceptance criteria and keep code/contracts/docs consistent.
3. Run relevant containerized checks; report commands, runtime/image, exit codes,
   skips, warnings, and limitations.
4. Record sanitized evidence separately from private media, backups, and secrets.
5. Prepare a reviewable PR using the repository template; wait for CI and review.
6. Never push directly to `main`, force-push, merge, or deploy automatically.

See [Definition of Done](./DEFINITION-OF-DONE.md) and
[testing strategy](./TESTING-STRATEGY.md).
