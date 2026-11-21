# OJU Video Forge - Agent Onboarding

Canonical repository instructions for Kilo Code, GitHub Copilot, and any other coding
agent. Tool-specific files (`.github/copilot-instructions.md`,
`.github/instructions/*`, `.github/agents/*`) add scoped detail and apply the same
boundaries; this file is authoritative on *implementation status* and *evidence*.
Do not restate this document in tool-specific files - link to it.

## 1. Project identity

OJU Video Forge is a local-first, containerized video production and AI-assisted
training-video system. Approved procedures become a narrated training video through a
persisted, human-reviewed pipeline. Nothing leaves the host: no Azure, cloud, or
external-identity dependency is required.

Implemented in the checked-out repository (path = evidence, not a completion claim):

| Capability | Implementation |
| --- | --- |
| Local accounts, password sessions, revocation | `app/auth.py`, `app/api.py` (`/v1/auth/register`, `POST/DELETE /v1/auth/session`) |
| Project membership roles (owner/reviewer/editor/viewer) | `app/auth.py`, `app/core.py` (`Project`, `ProjectMembership`), `app/api.py` |
| Durable job records and success-only job authorization | `app/core.py` (`Job`, state machine `TRANSITIONS`) |
| Durable dispatch outbox + dispatcher loop | `app/core.py` (`DispatchOutbox`), `app/dispatcher.py`, `services/worker` |
| Render worker: timeline/annotations/audio -> FFmpeg | `app/worker.py`, `app/rendering.py`, `services/worker/Dockerfile` |
| Media storage, project-scoped artifacts, checksums | `app/private_storage.py`, `app/core.py` (`MediaAsset`, `MediaArtifact`, `WorkflowArtifact`) |
| Narration: procedure evidence -> script -> TTS -> render | `app/narration.py`, `app/narration_jobs.py`, `app/audio_workflows.py` |
| Training-draft generation + agent adapter (disabled by default) | `app/agents.py`, `app/training.py` |
| Human review: evidence, training script, final video | `app/review.py`, `app/api.py` (`/procedure-review`, `/approval`, `/review`), immutable `Review` rows |
| Provenance (steps, model identity, media SHA-256) | `app/narration.py`, `app/worker.py`, `app/core.py` (`Narration`, `NarrationRevision`) |
| HMAC-signed host telemetry | `app/host_telemetry.py`, `scripts/collect-host-telemetry.ps1`, `scripts/publish-host-telemetry.py` |
| Redis inference leases + telemetry-gated admission | `app/inference_coordination.py` |
| ComfyUI GPU adapter (Wan 2.2 / image) | `app/comfyui.py`, `services/comfyui/`, `compose.gpu.yaml` |
| Schema | `alembic/versions/0001`...`0011` |

Not to be described as complete: strict end-to-end inference exclusivity
(BLOCKED, ADR-OVF-003 Proposed), H.264 as the production render default
(PARTIAL - see S5), publication/external release (NOT TESTED), browser-driven
upload-to-download acceptance (BLOCKED gate), and backup restoration on real data
(NOT TESTED). Each claim above is a *source* claim; acceptance claims come only
from S3.

## 2. Evidence hierarchy for status claims

Use this order. A lower-numbered source overrides a higher one, and a higher one may
never be used to contradict a lower one.

1. Current checked-out source and Git state (`git rev-parse HEAD`, `git status --short`).
2. Executable tests and validators actually executed in this session.
3. Verified runtime evidence (running images by digest, captured logs, FFprobe output).
4. Accepted architecture decisions and canonical acceptance registers
   (`docs/reviews/ERE-ACCEPTANCE-REGISTER.yaml`, `docs/ere-development-baseline-register.md`).
5. Current developer documentation (`docs/development/*`, `docs/operations/*`).
6. Historical plans, issue text, and older reports (`docs/implementation/VERTICAL-SLICE-PLAN.md`,
   `docs/implementation-plan.md`, `docs/reviews/ERE-FINAL-IMPLEMENTATION-REPORT.md`,
   `docs/reviews/ERE-REMAINING-GAPS.md`, `docs/reviews/IMPLEMENTATION-GAP-ANALYSIS.md`,
   `docs/reviews/ERE-BASELINE-INVENTORY.md`, `docs/phase-14-implementation-acceptance-report.md`).

Rules:

- Source code proves intent and wiring, not runtime acceptance. Running containers
  prove a binary ran, not that it was built from the current commit.
- Historical documents describe a past state and never override newer verified
  evidence. Treat statements like "auth source edits were denied", "frontend missing",
  "no Git commit exists" as history, not current fact.
- This repository was an early scaffold at one point. It is not one now. Never
  report "missing/early scaffold" without first naming the module and test that show
  it is absent.
- When evidence conflicts, **record the discrepancy** in your report (both sources and
  which one you believe, with reason). Never silently pick one or guess.
- Prefer the acceptance register's status vocabulary: PASS / PARTIAL / BLOCKED /
  NOT TESTED / NOT VERIFIED. Do not upgrade a status.

## 3. Repository navigation

- API surface: `app/api.py` (`/v1/*`, plus `/health/live|ready|stack`). OpenAPI is
  exported with `python scripts/export_openapi.py`; there is no generated client.
- Persistence: `app/core.py` (models, transitions, `SessionLocal`, `queue`,
  `install_review_guards`); migrations in `alembic/versions/`.
- Dispatch: `app/dispatcher.py` (`dispatch_outbox`, `dispatch_due`,
  `recover_undelivered_jobs`, `recover_stale_jobs`) and `app/core.py`
  (`DispatchOutbox`, `recover_jobs`). RQ queue names: `video`, `gpu`.
- Rendering: `app/rendering.py` (engine, subtitle burn-in), `app/worker.py`
  (`execute`, `_stage_timeline_inputs`, `_cleanup_render_output`).
- Narration/TTS: `app/narration.py`, `app/narration_jobs.py`, `services/narration/`,
  `compose.narration.yaml`.
- Training/agents: `app/training.py` (draft schema + branching procedures),
  `app/agents.py` (adapter; disabled unless `AGENTS_ENABLED=true`).
- Review: `app/review.py`; immutable review rows are enforced by DB triggers
  (`install_review_guards`) and the `Review` model.
- Telemetry/inference: `app/host_telemetry.py`, `app/inference_coordination.py`
  (`heavy_inference_lease`, `read_trusted_telemetry`, `preflight_heavy_inference`).
- Frontend: `frontend/src/api`, `frontend/src/ui`, tests via `npm test` (vitest).
- Tests: `tests/` (pytest). Scripts: `scripts/` (validators, acceptance, smoke).

## 4. Development environment

- ERE host: Windows 11 workstation, Docker Desktop with WSL2 Linux containers.
  Canonical Compose project name `oju-video-forge`; isolated `-acceptance` and
  `-integration` projects exist and must not share volumes, networks, or ports.
- Backend: Python 3.12 FastAPI + SQLAlchemy 2 + Alembic, Postgres 17, Redis 7 + RQ.
- Frontend: React + Vite + TypeScript (`frontend/`), served from its own container.
- Media: FFmpeg/FFprobe in the worker image; media stays on private volumes/networks.
- Inference: dedicated OJU Ollama service (`compose.ollama.yaml`, endpoint
  `http://ollama:11434`, `TRAINING_TEXT_URL`). Never the ERE host Ollama.
- GPU: optional NVIDIA profile (`compose.gpu.yaml`, ComfyUI CUDA image) with
  telemetry-gated admission; GPU availability is never inferred from a profile file.
- Telemetry: PowerShell collector on the host, HMAC-SHA256 signed envelope, consumed
  by the API/worker through `read_trusted_telemetry` (fails closed).
- Compose layering: `compose.yaml` + one of `compose.dev.yaml`, `compose.gpu.yaml`,
  `compose.acceptance.yaml`, `compose.integration.yaml`, `compose.ollama.yaml`,
  `compose.narration*.yaml`.

## 5. Architectural decisions

- ADR-OVF-001 (service architecture): **PROPOSED - NOT APPROVED**.
- ADR-OVF-002 (hosting/organizational boundary): **PROPOSED**.
- ADR-OVF-003 (`docs/architecture/decisions/DUAL-ROLE-ARCHITECTURE.md`, dual-role AI
  vision + training text): **Proposed**. Strict end-to-end inference exclusivity
  remains **BLOCKED** - see `docs/adr-ovf-003-gateway-proposal.md`.
- Do not implement an execution gateway. Passing Redis lease tests
  (`tests/test_inference_coordination.py`) proves admission serialization only; a
  lease cannot interrupt an in-flight Ollama request. Never report that as resolving
  the architectural limitation.
- H.264: the media layer encodes H.264/AAC (`app/rendering.py` `RenderEngine`;
  `app/narration.py` `mux()` when render_profile is `h264_aac`). It is qualified at
  the direct `media.mux()` level and in an authenticated API-to-worker path, but
  `render_profile` defaults to `source_passthrough` (`app/api.py`, `app/narration.py`)
  and the timeline endpoint encodes only when `render_annotations=True`
  (`app/api.py`). Describe H.264 as **PARTIAL**, not "the default".
- Governance work never approves an ADR; only an accountable owner does.

## 6. Agent execution rules

1. Inspect the current repository before proposing changes: `git rev-parse HEAD`,
   `git status --short`, then the relevant module and its tests.
2. Reuse existing implementation. Do not add a duplicate endpoint, model, schema,
   service, queue, or validator when one already exists.
3. Make scoped corrections; smallest complete change; preserve unrelated files and
   uncommitted work. Never reformat or "clean up" code you were not asked to touch.
4. Add or extend focused regression tests for behavior you change.
5. Validate with the commands in S7 that cover your change, and state exactly what you
   ran, the exit code, and what you did not run.
6. Create logical local commits only when validated; never push, merge, rebase
   published history, or force-push unless explicitly asked.
7. Do not stop for approval on read-only inspection, small edits within scope, or
   local test runs. Only ask when an action is destructive, externally visible, or
   touches shared infrastructure.
8. Deliver one consolidated execution report: SHAs, files, commands/results,
   discrepancies, and remaining gaps. Do not narrate plans instead of executing.
9. Use only the tools actually exposed in your current session. If a named tool (for
   example a ranged file view) is not available, read the file with the available
   read capability and note the substitution. Do not fabricate tool output.
   Tool exposure and the 128-tool limit are covered in
   `docs/development/AGENT-TOOL-PROFILE.md`.

## 7. Testing and validation

Run from the repository root on Windows PowerShell; activate `.venv` first if present
(`.\.venv\Scripts\Activate.ps1`). Commands verified against this checkout:

- Python tests: `python -m pytest -q -p no:cacheprovider`
  DB/Redis-dependent suites (`tests/test_job_dispatch.py`, `tests/test_api_gpu.py`,
  `tests/test_gates_2_5_integration.py`) require `APP_ENV=test`, a `*_test`
  PostgreSQL database, and Redis database 15; otherwise they skip by design.
- Ruff: `python -m ruff check app tests alembic scripts`
- Compile: `python -m compileall -q app tests alembic scripts`
- Repository validation: `python scripts/validate_repository.py`
- Acceptance register: `python scripts/validate_acceptance_register.py`
- Dependency locks: `python scripts/check_dependency_locks.py`
- OpenAPI export: `python scripts/export_openapi.py --output <path>`
- Compose (never starts services):
  `docker compose --env-file .env.development.example -f compose.yaml -f compose.dev.yaml config --quiet`
  and the same for `compose.gpu.yaml`.
- Frontend (`cd frontend`): `npm run typecheck`, `npm test`, `npm run build`.
- Application integration: `.\scripts\acceptance.ps1 -Environment acceptance`
  (isolated Compose project, synthetic media) and `python scripts/verify_dev_stack.py`.
- Do not record passing totals as permanent facts; report the count you observed with
  its command, date, and environment.

## 8. Security boundaries

Never do any of the following:

- Bypass, weaken, or stub authentication/authorization; frontend visibility is not an
  authorization control.
- Impersonate a human reviewer or invent approval decisions to advance a real
  acceptance case. Tests may use isolated fixtures; real content needs a real person.
- Print, commit, or expose bearer tokens, passwords, HMAC keys, or telemetry keys.
- Modify the ERE host Ollama installation, models, or the OJU Ollama service state
  without explicit approval; no model downloads without approval.
- Disable resource admission, remove/relax inference leases, or bypass
  `preflight_heavy_inference`.
- Run destructive database operations (drop/truncate/reset volumes, downgrade past
  0007, delete evidence) or migrate production/shared data.
- Push, merge, force-push, or rewrite published history without explicit instruction.
- Touch NuSass workloads or any other non-OJU workload, host service, or container.
- Commit credentials, private media/prompts, raw host inventories, or private logs.

## 9. Human-review workflow

Three separate approval boundaries, with separate endpoints and actors:

1. **Procedure evidence** - `/v1/projects/{id}/narrations/{nid}/procedure-review`
   (approve/reopen extracted steps). Gates training-draft generation.
2. **Training script** - `/v1/projects/{id}/narrations/{nid}/approval` (revision-bound;
   any subsequent edit invalidates it). Gates render submission.
3. **Final rendered media** - `/v1/jobs/{job_id}/review` and
   `/v1/projects/{id}/.../reviews` with artifact checksum verification.

Reviewers must be project owners/reviewers and must not be the requester or an agent
service identity. Agents may prepare review *packages* (summaries, checklists,
evidence bundles), inspect decisions, and exercise authorization with isolated
fixtures - but must not record approvals against real product content.

## 10. Evidence standards

For any completion or acceptance claim, include:

- Git SHA (starting and ending) and worktree status.
- Exact commands, exit codes, and counts actually observed.
- Running image identity by digest (`docker inspect --format '{{.Image}}'`), not tag.
- Model name + digest for any inference claim.
- Media SHA-256 and FFprobe output for any rendering claim.
- Reviewer identity and decision for any approval claim.
- Runtime acceptance details (environment, date, data source).

Keep the levels distinct: unit test != mocked integration != direct media-library run !=
authenticated API-to-worker acceptance != browser acceptance. Say which one you have.

## 11. Dispatch and outbox reality (verify before claiming)

`DispatchOutbox` rows are written in the same PostgreSQL transaction as the job;
Redis enqueue happens afterwards in `dispatch_outbox()`. There is **no atomic
PostgreSQL+Redis transaction**, and RQ does not retry the enqueue for you - the
dispatcher loop (`app/dispatcher.py` `run()`) re-drives it on the next pass.

What exists today (each has a test in `tests/test_job_dispatch.py`): retry with
backoff plus a `dispatch_failed` audit row on enqueue failure; a 60s dispatch lease
so a stale worker cannot settle a newer claim; per-job dedupe by RQ job id, so a
restarted dispatcher reuses the existing delivery; a worker status guard against
duplicate execution; and job-level recovery of lost RQ deliveries
(`recover_undelivered_jobs`). A crash between the commit and the enqueue leaves the
outbox row `pending`, which `dispatch_due()` picks up next pass.

Stale recovery is driven purely by the age of `updated_at`:
`recover_stale_jobs()` fails a `running` job with `worker_lost` at
`STALE_WORKER_SECONDS` (`QUEUE_JOB_TIMEOUT` + 300 = 3900s) or
`GPU_STALE_WORKER_SECONDS` (`GPU_QUEUE_JOB_TIMEOUT` + 300 = 7500s). Every
long-running path now carries an in-band liveness signal through
`app/worker.py` `_JobHeartbeat`: the timeline harness ticks it from
`check_cancel()`, narration ticks it from `check_narration()`, and GPU generation
ticks it before the admission wait and once per ComfyUI poll via
`ComfyUIAdapter.wait_for_completion(on_poll=...)`. The tick is rate-limited
(5s) and resolves `updated_at` only while the row is still `running` **and** still
carries the `review_version` snapshotted when the heartbeat was constructed
(attempt fencing: `recover_jobs()` can return the same row to `running`, so
status alone would let a displaced attempt keep a newer one alive), and a
heartbeat database error is logged but never fails a healthy render. Covered by
`tests/test_job_heartbeat.py` (DB-backed). The concurrency model is unchanged -
do not add background threads to this path.

Coverage is **in-band** and that limit must be stated, not hidden. A tick only
happens where a `check()` hook runs, so it does not fire during a blocking call
that returns nothing in between: a model request whose response body arrives only
at the end (the normal shape of an Ollama completion) and a single long
`espeak-ng`/FFmpeg invocation get no tick while they block. Narration's
`heavy_inference_lease` wait also re-checks only every 5s, and a task that is not
started does not tick at all. `tests/test_heartbeat_streaming_gap.py` records
both directions (a chunked body does tick; a silent body does not).
