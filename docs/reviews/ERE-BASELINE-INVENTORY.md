# ERE Baseline Inventory

**Observed:** 2026-11-05  
**State:** Read-only baseline; no application files changed during discovery.

## Repository

- Branch: `main`.
- `HEAD` does not resolve; there is no initial commit.
- The repository working tree consists of untracked files. There is no committed baseline to compare against.
- A recoverable archive was requested before implementation, but permission to create it was denied. No alternate archive mechanism was attempted.
- Repository metadata reported 429 files and 1.83 MiB at inventory time.
- No `AGENTS.md` was found. Repository-specific guidance exists at `.github/copilot-instructions.md`.
- `ADR-OVF-001.md` remains proposed; no ADR was accepted. `ADR-OVF-002.md` was absent at baseline and has since been added as proposed; neither ADR is accepted.

## Project components found

| Area | Baseline evidence | Classification |
|---|---|---|
| API | `app/api.py` has liveness, model listing, job submission, and owner-scoped job status. | Partially implemented |
| Authentication | `app/auth.py` accepts configured shared service bearer tokens; OIDC/Azure selections return 501. No local account/session implementation was found. | Partially implemented |
| Configuration | `app/config.py` provides typed settings and startup validation. Runtime configuration was not exercised. | Implemented, runtime unverified |
| Data and migration | `app/core.py` defines job/audit models and status transitions; `alembic/versions/0001_initial.py` creates job and audit tables. Live migration was not run. | Partially implemented |
| Queue/worker | API persists a job before queue dispatch and can report `dispatch_pending`; RQ/worker integration exists. Crash recovery, retry, cancellation, and reconciliation were not validated. | Partially implemented |
| FFmpeg | `app/worker.py` contains a deterministic test-clip path. End-to-end timeline editing/rendering is absent or unverified. | Partially implemented |
| ComfyUI | Worker can submit a WAN prompt using a configured workflow/node mapping. Prompt submission is not output retrieval or validation. | Partially implemented |
| Frontend | No frontend application files were found. | Missing |
| Compose | `compose.yaml` defines API, worker, optional GPU-profile ComfyUI, PostgreSQL, and Redis. Development Compose binds API/DB/Redis to loopback. These definitions do not prove services run. | Implemented, runtime unverified |
| Tests | `python -m pytest -q` was reported as 2 passed by the read-only audit. These are limited contract/unit checks, not end-to-end coverage. | Implemented, limited |
| Docs/API contract | README, proposed ADR, DNS design, workflow configuration, and OpenAPI YAML exist. Contract completeness and OpenAPI 3.1 compatibility were not validated. | Partially implemented |

## Existing host observations

The local host reported hostname `ere`, Windows 11 Enterprise, an Intel Core Ultra 9 285, 31.38 GiB RAM, RTX 5070 Ti with 16,303 MiB VRAM, Docker Desktop Linux engine, and Ollama 0.40.14. ComfyUI refused local connections on port 8188. The hostname is consistent with ERE, but organizational assignment was not independently verified. See [the environment inventory](../operations/ere-environment-inventory.md).

## Verification commands reported

| Command | Result | Scope |
|---|---|---|
| `python -m pytest -q` | 2 passed | Limited existing tests; not an integration suite |
| `git status --short --branch` | `main`, all listed project paths untracked | No committed baseline |
| `git rev-parse --verify HEAD` | Failed: no resolvable revision | Confirms no initial commit |
| `docker compose config --quiet` | Not run | Compose runtime configuration unverified |

After the initial audit, both `docker compose -f compose.yaml -f compose.dev.yaml config --quiet` and `docker compose -f compose.yaml -f compose.gpu.yaml config --quiet` completed with exit code 0. This validates Compose configuration only; it does not start services or verify GPU support.

## Changes after baseline capture

Before the parallel write tasks were stopped, agents added `app/rendering.py`, `tests/test_rendering.py`, `docs/rendering-contract-v1.md`, `app/comfyui.py`, and a new `frontend/` application. These additions are partial and unintegrated; their validation status is recorded in the interim implementation report. No existing services, volumes, models, or media assets were changed.

No containers, volumes, model files, media assets, or remote infrastructure were changed.
