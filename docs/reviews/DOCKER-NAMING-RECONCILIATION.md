# Docker naming reconciliation

Observed: 2026-11-07. **Status: PARTIAL / canonical runtime active.**
Source baseline: `d23a7d61ba694d190a6dd5bb31a6edefc8d80ac6`, branch
`feature/ere-docker-rendering-vertical-slice`. Changes are in the working tree,
not committed or published. The owner-approved disposition was FRESH-PRESERVE:
create a fresh canonical database and retain all historical acceptance and
integration data. The owner also approved the loopback ports 5173/5174/5175 and
stopping only the port-owning `oju-phase3-final` frontend after backup verification.

## Findings and data disposition

- `compose.yaml` already declares `oju-video-forge`. Earlier integration commands
  intentionally supplied `-p oju-phase3-acceptance`, creating a separate synthetic
  test environment rather than selecting canonical application storage.
- At the start, `oju-phase3-final-frontend-1` owned loopback port 5173. After the
  backup restore gate passed, that frontend container was stopped but retained;
  no existing container or volume was removed.
- Port 5173 is now served by the canonical `oju-video-forge` frontend. The other
  historical `oju-phase3-final` services and volumes were left untouched.
- The acceptance outage diagnostic was a Compose one-off running the synthetic
  smoke script. It finished successfully and its `--rm` lifecycle completed before
  this request. The observed `api-run-7603105d008d` is no longer present.
- Original acceptance and integration volumes remain intact. Historical acceptance
  data was restored only into disposable isolated volumes and was not copied into
  canonical storage.
- Canonical storage was freshly created. Its separate browser smoke-test account
  is disabled and its session revoked; no historical synthetic accounts or media
  were migrated.
- No shell `COMPOSE_*` override was observed. Private credentials were not printed.
  Database/password/session contents and raw host inventories are not published.
- Unrelated services were inspected only for coexistence and port conflicts;
  none were modified.

## Current runtime

| Canonical service | Observed state |
|---|---|
| PostgreSQL | Healthy; canonical `pgdata`; Alembic head `0009` |
| Redis | Healthy; canonical `redisdata`; private network |
| API | Healthy; canonical upload/artifact volumes; private backend/control networks |
| Dispatcher | Running; private control network |
| Render worker | Healthy; corrected image; database/Redis connected; no restart loop |
| Frontend | Running; only canonical loopback publication `127.0.0.1:5173` |
| Migrate | Exited 0 after applying migrations to the fresh canonical database |

The optional GPU profile was not enabled. The `oju-video-forge-acceptance` stack
was stopped after its smoke test; its containers and volumes remain. The
`oju-phase3-final` render worker remains in its historical state and was not
reconciled or modified.

## Isolation changes

The [environment runbook](../operations/DOCKER-ENVIRONMENTS.md) defines:

- Canonical `oju-video-forge`: frontend 5173, private `.env`.
- Acceptance `oju-video-forge-acceptance`: frontend 5174, private `.env.acceptance.local`.
- Integration `oju-video-forge-integration`: frontend 5175, private `.env.integration.local`.

Rendered Compose configurations confirm independent project-prefixed volumes and
networks and exactly one loopback frontend port per environment. No explicit
container names, external volumes/networks, or public database/queue/inference
ports are configured. Canonical and acceptance runtime labels, storage, networks,
and loopback publication were verified after startup; integration was config-only.

PowerShell/Bash helpers reject project/file/storage overrides, conflicting/shared
database secrets, orphan removal, and destructive commands. Synthetic acceptance
checks actual labels, volume/network namespaces, and writable bind mounts before
writing; the smoke script rejects canonical/non-test settings.

## Private backup evidence

At `2026-11-07T17:55:01Z`, six private backup archives were verified by manifest
SHA-256 and size. A second private copy was placed outside Git and session-state,
with restrictive account/SYSTEM/Administrators access; its file hashes match the
manifest. Original archives and source volumes remain unchanged.

| Backup | Bytes | Verification |
|---|---:|---|
| Acceptance PostgreSQL offline snapshot | 50,167,808 | Read-only source; archive readable; SHA-256 recorded |
| Acceptance media/artifacts | 2,810,880 | Read-only source; archive readable; SHA-256 recorded |
| Acceptance Redis persistence | 34,304 | Read-only source; archive readable; SHA-256 recorded |
| Current empty database logical dump | 46,329 | `pg_dump -Fc`; `pg_restore --list` exit 0 |
| Current empty artifacts | 2,048 | Archive readable; SHA-256 recorded |
| Current empty uploads | 1,536 | Archive readable; SHA-256 recorded |

`pg_controldata` on the original read-only acceptance volume reports PostgreSQL
17 catalog metadata and **Database cluster state: shut down**. This supports a
consistent offline snapshot. Disposable restore verification then passed using
isolated containers and newly created volumes: the physical PostgreSQL 17 snapshot
started successfully at Alembic revision `0009` with 16 public tables; the logical
dump restored separately at revision `0009` with the expected tables and no
users/projects/jobs; Redis AOF loaded 5 keys; and all 24 restored artifact/media
files matched their archived hashes, with 20 media files passing FFprobe. No
source volume was mounted writable. Restore containers are stopped and their
isolated restore volumes are retained. The durable private copy has a separate
restore-evidence addendum; original manifests and archives are unchanged.

## Validation

Environment: Windows workstation, Docker Desktop Linux containers; Python 3.12
Alpine test/worker images, approved FFmpeg 6.1.2 package; no host application install.

| Command | Exit | Actual result |
|---|---:|---|
| `docker compose -f compose.yaml -f compose.acceptance.yaml config --quiet` | 0 | Valid configuration; no services started |
| `docker compose -f compose.yaml -f compose.integration.yaml config --quiet` | 0 | Valid configuration; no services started |
| `docker compose -f compose.yaml -f compose.dev.yaml config --quiet` | 0 | Valid configuration; no services started |
| `.\scripts\forge.ps1 -Environment application -Plan` (also acceptance/integration) | 0 | Correct explicit project/file/secret selection |
| PowerShell AST parsing of both helpers | 0 | No syntax errors |
| Containerized `ruff check --no-cache app tests alembic scripts` | 0 | Passed |
| Containerized focused auth/media/render/naming suite | 0 | 27 passed; 1 Starlette/httpx warning |
| `docker build --target test -t oju-video-forge-tests:reconciliation -f services/api/Dockerfile .` | 0 | Test image built |
| `docker run --rm --network none oju-video-forge-tests:reconciliation python -m pytest -q -p no:cacheprovider -rs` | 0 | 229 passed, 12 skipped, 5 warnings |
| `docker build -t oju-video-forge-render-worker:reconciliation -f services/worker/Dockerfile .` | 0 | Corrected worker built |
| `docker run --rm --network none oju-video-forge-render-worker:reconciliation python -c 'import app.worker; print("Worker import PASS")'` | 0 | Import passed |
| `docker compose --env-file .env.development.example -f compose.yaml config --quiet` (also acceptance, integration, and GPU overlays) | 0 | All four configurations valid; placeholder values used for config-only validation |
| `docker run --rm --network bridge --entrypoint /bin/sh oju-video-forge-tests:reconciliation -c 'pip-audit -r requirements.lock.txt && pip-audit -r requirements-dev.lock.txt'` | 0 | No known backend dependency vulnerabilities |
| Containerized frontend validation | 0 | Typecheck/build passed; 54 tests passed; npm audit reported 0 vulnerabilities |
| `.\scripts\acceptance.ps1 -Environment acceptance` | 0 | Full synthetic API-to-MP4 acceptance passed; 42,143-byte MP4; SHA-256 `02a09e63daba5b254e80c589bd7d45de284ee5c4a7baa64c6894ba97de15b4b9`; FFprobe verified H.264 640x360/24 fps/yuv420p plus AAC stereo 48 kHz, 2 seconds |
| Browser UI checks through Playwright | 0 | Registration, login, project creation, and logout passed; no browser console errors |
| Canonical browser upload/render UI flow | n/a | Not verified: browser file-upload tool denied access to the synthetic file path; no alternate upload route used |
| Disposable restore verification | 0 | PostgreSQL physical/logical, Redis AOF, and artifact/media restores passed as summarized above |

Compose validation used a process-local configuration placeholder, not a startup
credential. The 12 skipped cases require isolated `*_test` PostgreSQL and Redis DB
15; no database integration pass is claimed for this offline run. Five warnings
are existing Starlette/httpx and SQLite datetime adapter deprecations.

## Current acceptance status and remaining gaps

- **PASS:** private archive integrity and disposable restore verification; fresh
  canonical database and migration head `0009`; isolated Compose names/storage/
  networks/ports; canonical API, worker, and frontend health; local registration,
  login, project creation, session revocation, and cross-user denial; full
  authenticated API upload-to-render-to-authorized-artifact workflow; FFprobe,
  output checksum, and audit evidence.
- **PARTIAL:** browser-driven product flow. The browser verified canonical
  registration/login and project creation, but its file-upload tool denied access
  to the synthetic media path. Browser upload, render submission/progress, and
  artifact retrieval therefore remain unverified; backend API acceptance passed.
- **NOT_VERIFIED:** GitHub CI and security scans at a published feature-branch
  commit; a browser-driven upload/render flow; optional GPU inference. No PR was
  created as part of this local validation.
- `oju-phase3-final` and all historical volumes remain retained; only its
  port-owning frontend was stopped. No unrelated service, historical data, or
  volume was removed.
