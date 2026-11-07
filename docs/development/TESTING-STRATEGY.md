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

## Issue #12: owner-scoped review bridge v1

This increment deliberately uses `jobs.owner`, not projects: dependencies #8/#9
are OPEN separate WIP PRs and are absent from this checkout. Existing `/auth/*`
and `/projects` frontend calls remain unfinished. The independent local-token
panel uses the current backend; it adds no login, project, upload, retrieval or
publishing infrastructure. New review tokens are memory-only.

Review reads require authentication and ownership; decisions additionally require
reviewer/administrator roles, including for admins. Only a nonempty generated
worker `render_test/test.mp4` with persisted identity and SHA-256 may transition
from awaiting review to approved/rejected. The API rehashes bounded, no-follow
regular files; old rows without evidence and ComfyUI submission JSON fail closed.
The SQL CAS includes owner/state/version/artifact identity, and job version,
review evidence and existing audit event commit atomically. Evidence is append-only
at the DB layer with unique `(job_id, version)` and version/decision constraints.
No private prompts/media/paths/comments are recorded in review history.

`REVIEW_WORKFLOW_ENABLED=true` is the documented default. Disable and restart the
API to return 503 for authorized decisions; reads/history remain available and
no job is automatically approved/unblocked/reset. Migration `0002` preserves
existing rows/audits with review version zero and null legacy artifact evidence.
Downgrade always refuses destructive removal; disable the feature rather than
rolling back schema. This is not superuser-resistant or cryptographic evidence.

### Original local evidence — 2026-11-06 (before architecture follow-up)

Base commit `5ef45efd0456da2276f8f9ac14dc503cc92599c0`, branch
`copilot/add-review-approval-workflow`, uncommitted issue changes. Linux,
Python 3.12.3, Node 24.22.1 (not CI's Node 26), npm 11.19.1, pytest 9.0.3,
Vitest 4.1.12, Vite 6.4.5. No dependency manifests/locks changed.
Commands below ran from `/home/runner/work/OJU-Video-Forge/OJU-Video-Forge`,
unless the frontend working directory is specified. All final checks exited 0
except the **expected** destructive downgrade refusal (exit 1).

| Actual command | Original result (superseded by follow-up below) |
|---|---|
| `python -m venv /tmp/ovf-review-venv` then `/tmp/ovf-review-venv/bin/python -m pip install --require-hashes -r /home/runner/work/OJU-Video-Forge/OJU-Video-Forge/requirements-dev.lock.txt` | Locked environment installed |
| `/tmp/ovf-review-venv/bin/python -m pip check` | No broken requirements |
| `/tmp/ovf-review-venv/bin/pip-audit -r /home/runner/work/OJU-Video-Forge/OJU-Video-Forge/requirements.lock.txt` and the same command with `requirements-dev.lock.txt` | Both: no known vulnerabilities |
| `PYTHONPYCACHEPREFIX=/tmp/ovf-review-pycache /tmp/ovf-review-venv/bin/python -m pytest -q -p no:cacheprovider tests/test_review.py` | **64 passed**, 1 warning |
| `/tmp/ovf-review-venv/bin/ruff check app tests alembic scripts` | All checks passed |
| `PYTHONPYCACHEPREFIX=/tmp/ovf-review-pycache /tmp/ovf-review-venv/bin/python -m pytest -q -p no:cacheprovider` | **133 passed, 1 skipped**, 1 warning |
| `PYTHONPYCACHEPREFIX=/tmp/ovf-review-pycache /tmp/ovf-review-venv/bin/python -m compileall -q app tests alembic scripts` | Passed |
| `PYTHONPYCACHEPREFIX=/tmp/ovf-review-pycache /tmp/ovf-review-venv/bin/python scripts/export_openapi.py --output /tmp/ovf-review-openapi.json` | Actual generated OpenAPI validated |
| `/tmp/ovf-review-venv/bin/python /home/runner/work/OJU-Video-Forge/OJU-Video-Forge/scripts/check_dependency_locks.py` | Both committed locks match manifests |
| `/tmp/ovf-review-venv/bin/python /home/runner/work/OJU-Video-Forge/OJU-Video-Forge/scripts/validate_acceptance_register.py` | Schema passed; not acceptance approval |
| `npm ci` in `frontend/` | 94 packages added, 95 audited, 0 vulnerabilities |
| `npm audit --audit-level=moderate` in `frontend/` | 0 vulnerabilities |
| `npm run typecheck` in `frontend/` | Passed |
| `npm test -- src/api/reviewController.test.ts src/api/client.test.ts` in `frontend/` | Initial targeted UI run: 21 passed (before final additional stale-session regression) |
| `npm test` in `frontend/` | Final: **22 passed**, 2 files |
| `npm run build -- --outDir /tmp/ovf-review-frontend-build` in `frontend/` | Typecheck and production build passed, 32 modules |
| `docker compose --env-file /home/runner/work/OJU-Video-Forge/OJU-Video-Forge/.env.development.example -f /home/runner/work/OJU-Video-Forge/OJU-Video-Forge/compose.yaml -f /home/runner/work/OJU-Video-Forge/OJU-Video-Forge/compose.dev.yaml config --quiet` | Passed; no services started |
| Same Compose command with `compose.gpu.yaml` instead of `compose.dev.yaml` | Passed; not GPU validation |
| `git --no-pager diff --check` | Passed |

Additional exact schema/config checks:

```sh
/tmp/ovf-review-venv/bin/python -c 'from scripts.validate_repository import validate_yaml, validate_action_pins, validate_environment_templates; validate_yaml(); validate_action_pins(); validate_environment_templates(); print("PASS: YAML, action pins, environment templates (no agent access)")'
/tmp/ovf-review-venv/bin/python -c 'import yaml; from openapi_spec_validator import validate_spec; from pathlib import Path; validate_spec(yaml.safe_load(Path("/home/runner/work/OJU-Video-Forge/OJU-Video-Forge/config/chatgpt-actions.openapi.yaml").read_text())); print("PASS: legacy adapter schema validated")'
```

Both passed. Repository-wide customization validation was not run; the targeted
configuration checks above and synthetic metadata unit tests passed.

The focused suite uses real FastAPI TestClient/local bearer authentication and
file-backed isolated SQLite. It covers approve/reject, role/owner matrices
(cross-owner admins included), strict payloads, invalid/terminal states,
stale/changed/empty/missing/legacy/unsafe artifacts, independent-session racing
CAS (one 200, one 409, one audit), cached-state CAS rechecks, rollback on either
audit-table insert failure, SQL evidence immutability, repeat worker delivery,
flag preservation, and old-database migration/downgrade preservation. Two
offline SQL migration tests generate SQLite/PostgreSQL DDL without connecting;
they do not validate PostgreSQL runtime behavior.

UI tests cover exact decision payload/authentication, busy/disabled/read-only
controls, history identity/actor/time/version presentation, 409/503 refresh without
resubmit, 401/403/error confidential-data clearing, and cleared in-flight sessions.
Rendering uses React server rendering plus controller/client tests, not a browser
automation or media preview claim.

Actual loopback API smoke used `/tmp/ovf_review_smoke.py` (synthetic temporary
script, not committed), isolated `/tmp` SQLite/artifacts, no Redis or external
services. Fresh upgrade command:

```sh
APP_ENV=test DATABASE_URL=sqlite:////tmp/ovf-review-final-smoke.db PYTHONPYCACHEPREFIX=/tmp/ovf-review-pycache /tmp/ovf-review-venv/bin/python -m alembic -c /home/runner/work/OJU-Video-Forge/OJU-Video-Forge/alembic.ini upgrade head
APP_ENV=test DATABASE_URL=sqlite:////tmp/ovf-review-final-smoke.db ARTIFACT_ROOT=/tmp/ovf-review-final-smoke-artifacts PYTHONPYCACHEPREFIX=/tmp/ovf-review-pycache /tmp/ovf-review-venv/bin/python /tmp/ovf_review_smoke.py seed
```

The server command was `/tmp/ovf-review-venv/bin/python -m uvicorn app.api:app
--host 127.0.0.1 --port 18712 --no-access-log`, with the same isolated DB/root,
`APP_ENV=test`, temporary synthetic local owner/admin token environment values,
and `PYTHONPYCACHEPREFIX=/tmp/ovf-review-pycache`.
`/tmp/ovf-review-venv/bin/python /tmp/ovf_review_smoke.py enabled` passed **11
real HTTP requests**: auth/ownership, metadata/history, stale version, approve,
reject and terminal-repeat denial. Restarting the same API with
`REVIEW_WORKFLOW_ENABLED=false`, then
`/tmp/ovf-review-venv/bin/python /tmp/ovf_review_smoke.py disabled`, passed **3
requests** proving 503 writes and unchanged approved status/version/history.
Smoke servers were stopped, not left running.

An earlier equivalent smoke DB also ran
`APP_ENV=test DATABASE_URL=sqlite:////tmp/ovf-review-smoke-12.db
/tmp/ovf-review-venv/bin/python -m alembic -c
/home/runner/work/OJU-Video-Forge/OJU-Video-Forge/alembic.ini downgrade base`:
exit **1**, intentional refusal; direct SQL verification preserved both decisions
and schema `0002`. The migration regression upgrades a populated `0001` DB and
checks original jobs/audits survive and destructive downgrade is refused.

Warnings/iteration: the first focused backend run had 37 failures/18 passes from
a malformed synthetic test authorization header; correcting the fixture restored
55 passes before further regressions were added. An offline migration probe
exposed `MockConnection.exec_driver_sql` incompatibility; fixed using SQLAlchemy
DDL, with two passing regressions. Final Python warning is the locked
Starlette/httpx TestClient deprecation; no dependency was added to silence it.
The one full-suite skip is real FFmpeg/FFprobe rendering: neither tool is present.
`npm ci` warned about esbuild's install script; Vite warned that the chosen `/tmp`
outDir is outside the project and is not emptied. No frontend lint script exists.

Remaining gates: PostgreSQL execution/concurrency/triggers, Redis dispatch,
Compose service startup/images/container scans, CodeQL/secrets/SBOM scanners,
real FFmpeg rendering, browser E2E, GPU/ComfyUI output reconciliation, ERE and
project/media integration were **not validated**. No shared migration, external
infrastructure access, deployment, commit, push, PR creation or merge occurred.

### Architecture follow-up — 2026-11-06

The same branch/base remains uncommitted. `review_version` is now explicitly a
dedicated monotonic **workflow version**, not a decision/audit count. Atomic
state/version CAS fences dispatch failure, worker claim, completion and failure.
Only one duplicate delivery can claim a queued job/create its output directory.
ORM version checks also reject stale job updates: a worker object loaded while
running cannot overwrite a subsequently approved/rejected job. Each relevant
application writer increments the stored version; review CAS, version increment,
review snapshot and existing audit insertion still use one commit, without
calling `core.transition`. Workers no longer dirty ORM artifact fields before
their fenced completion. Drain old writers before an approved upgrade.

History is bounded (`after_version=0`, `limit=50`, maximum 100), ordered/indexed by
`(job_id, version)`, and returns persisted review snapshots without joining mutable
job metadata. Both local actors have reviewer roles: this is service-account
self-review, not independent human signoff. `administrator` never overrides owner.

Hashes stream fixed private job files in bounded chunks, not request-supplied
buffers/paths. Legacy null hashes remain fail-closed. Persisted checksums do not
make disk bytes immutable: API mounts are read-only, worker output is created by
the claim winner, and operators must not mutate completed files, including during
hashing/commit. Privileged filesystem changes after the sampled hash are outside
the database fence; future artifact retrieval integration must honor this boundary.

The feature setting is cached per process and takes effect for new requests after
API restart. An already-admitted review may finish during graceful shutdown;
disabling does not cancel its transaction. The new interleaving test verifies
this boundary while another newly arriving decision returns 503. Destructive
downgrade refusal is unchanged.

The independently reachable panel clears credentials/job/history on actor change,
disconnect, unauthorized/error responses, explicit clear and unmount. Generations
fence old async results and prevent refreshes using another session's credentials.
After 409/503 refetch, decisions are blocked until refreshed evidence is explicitly
acknowledged; there is no automatic decision retry.

**Latest executed checks, superseding earlier counts** (same Linux/Python/Node,
warnings, skips and environment limitations):

| Exact command | Latest result |
|---|---|
| `PYTHONPYCACHEPREFIX=/tmp/ovf-review-pycache /tmp/ovf-review-venv/bin/python -m pytest -q -p no:cacheprovider tests/test_review.py` | **71 passed**, 1 deprecation warning |
| `RUFF_CACHE_DIR=/tmp/ovf-review-ruff-cache /tmp/ovf-review-venv/bin/ruff check app tests alembic scripts` | Passed |
| `PYTHONPYCACHEPREFIX=/tmp/ovf-review-pycache /tmp/ovf-review-venv/bin/python -m pytest -q -p no:cacheprovider` | **140 passed, 1 skipped**, 1 deprecation warning |
| `PYTHONPYCACHEPREFIX=/tmp/ovf-review-pycache /tmp/ovf-review-venv/bin/python -m compileall -q app tests alembic scripts` | Passed |
| `PYTHONPYCACHEPREFIX=/tmp/ovf-review-pycache /tmp/ovf-review-venv/bin/python scripts/export_openapi.py --output /tmp/ovf-review-openapi.json` | Passed |
| `npm run typecheck`, `npm test`, `npm run build -- --outDir /tmp/ovf-review-frontend-build` in `frontend/` | Passed; **26 tests**, 2 files; build 32 modules, same `/tmp` outDir warning |

New regressions cover duplicate execute interleaving, stale worker CAS/ORM writes
after both terminal decisions, dispatch acknowledgement failure racing a worker
claim, unchanged version on failed reviews, bounded/snapshot history, admitted
versus new requests during disable, disconnect/actor change, and mandatory renewed
review while a conflict-refreshed job remains eligible.

The first follow-up focused run had 4 failures/66 passes: three fixtures assumed
version zero after deliberate ORM mutations, and the dispatch fake used a duplicate
positional/keyword argument name. Fixtures now assert the dedicated workflow
version remains unchanged by denied reviews, and the dispatch interleaving really
runs; no security assertion was removed.

Fresh SQLite migration and real loopback HTTP smoke were repeated on current code
using the earlier exact commands with `/tmp/ovf-review-fenced-smoke.db` and
`/tmp/ovf-review-fenced-smoke-artifacts` in place of the earlier final-smoke paths.
Upgrade/seed passed; `/tmp/ovf-review-venv/bin/python /tmp/ovf_review_smoke.py enabled`
passed **11 requests**. Restarting that API with `REVIEW_WORKFLOW_ENABLED=false`,
then `/tmp/ovf-review-venv/bin/python /tmp/ovf_review_smoke.py disabled`, passed
**3 requests** preserving terminal state/version/history. Both servers stopped.
No dependency, service/deployment, branch/commit/PR or unverified-gate status
changed.

### Final source and security review — 2026-11-06

Implementation commit: `e9b251e0919bc1fc8ae626deba03f68ce8f5a9e1`.
The validation results above apply to this implementation. Read-only code review
reported no significant issues. Secret scanning of all 20 changed files found no
secrets. CodeQL analysis of Python and JavaScript reported **0 alerts**.
Committed-file inspection found only intended source, tests, configuration and
documentation, with no generated artifacts. These checks do not establish
PostgreSQL runtime, browser E2E, project integration or deployment acceptance.
