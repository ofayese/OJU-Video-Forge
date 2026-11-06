# Implementation Gap Analysis

**Observed:** 2026-11-05  
**Scope:** Repository evidence and read-only local host discovery. “Implemented” does not imply runtime validation.

## Status update after baseline

Partial files were subsequently added: a standalone renderer (`app/rendering.py`), its tests and contract, a ComfyUI adapter (`app/comfyui.py`), and a new React frontend. The renderer's focused tests include one Windows line-ending failure and one skip; the ComfyUI adapter is only syntax-checked; frontend checks did not pass because dependencies were unavailable. Backend auth/project edits were denied and were not made. These additions do not close the end-to-end gaps below.

| Capability | Classification | Relevant files / evidence | Required work and acceptance |
|---|---|---|---|
| Typed settings and startup validation | Implemented, runtime unverified | `app/config.py` | Exercise valid/invalid startup configurations and document supported settings. |
| Local user accounts and sessions | Missing | `app/auth.py`, `app/core.py`, `alembic/versions/0001_initial.py` | Persist users, hash passwords with Argon2id or equivalent, expire/revoke sessions, and test login/logout/recovery. |
| RBAC and project isolation | Partially implemented | `app/auth.py`, `app/api.py`, `app/core.py` | Add persisted roles/memberships and enforce authorization for every project/media/job/artifact read and write; cross-owner tests must deny access. |
| Projects and memberships | Missing | `app/core.py`, migration, `app/api.py` | Add project CRUD and membership model/endpoints with ownership tests. |
| Media upload and storage | Missing | API, storage layer, Compose | Add bounded validated uploads, safe names/paths, metadata inspection, checksums, and authorized retrieval tests. |
| Timelines and revisions | Missing | Core models, migrations, API, frontend | Versioned JSON schema, validation, edit operations, revision persistence, deterministic fixtures. |
| Job persistence and dispatch | Partially implemented | `app/api.py`, `app/core.py`, `app/worker.py` | Test Redis outage, duplicate/idempotent submission, cancellation/retry, worker restart recovery, timeout and terminal-state safety. |
| FFmpeg rendering | Partially implemented | `app/worker.py`, `compose.yaml` | Build validated timeline-to-FFmpeg rendering using argument arrays, timeouts, resource bounds, output probing/checksum, and authorized artifact retrieval. |
| AI video generation | Partially implemented; runtime blocked | `app/worker.py`, `config/workflows/`, `compose.gpu.yaml`; localhost ComfyUI refused connections | Verify exact installed ComfyUI/workflow/model, retrieve and validate generated files, register provenance; execute only when a compatible local service/model is available. |
| GPU admission and resource control | Missing / unverified | Worker, GPU Compose profile | Serialize or budget GPU work; test resource exhaustion/rejection and cancellation without disrupting existing workloads. |
| Narration, transcription, captions | Missing | No integrated local audio pipeline found | Validate licenses/provenance; implement local TTS/transcription, subtitle outputs, and timeline synchronization. |
| Frontend | Missing | No frontend files found | Implement real API-backed workflows and tests; no simulated operational metrics. |
| Review and approval | Missing | No approval workflow found | Persist reviewer decisions/comments, enforce roles, audit transitions. |
| OpenAPI and typed client | Partially implemented | `config/chatgpt-actions.openapi.yaml` | Validate OpenAPI version/schema, align endpoints/auth, generate or maintain typed client, test local API integration. |
| Observability and audit | Partially implemented | `app/core.py`, API | Add structured/correlated logs, worker heartbeats, metrics and tamper-resistant audit controls; verify provenance completeness. |
| Backup and recovery | Missing | No verified backup/restore procedure | Discover an actual destination, implement backups, verify integrity, and restore in isolation without deleting volumes. |
| Security and supply chain | Partially implemented / unverified | Auth, API, Compose, requirements | Test authorization, upload/path safety, rate limits, CORS/CSRF applicability, secret redaction, service isolation, dependency/container scans. |
| Developer workflow | Partially implemented | README, scripts, Compose, `.vscode/` | Verify reproducible setup/test/lint/typecheck/health commands and VS Code run/debug support on the observed host. |
| Hosting boundary ADR | Missing at baseline; proposed record added | `ADR-OVF-001.md`, `ADR-OVF-002.md` | Validate ERE ownership and applicable gates; keep both ADRs proposed until an accountable owner approves. |

## Immediate blocking facts

1. The requested pre-change recoverable checkpoint could not be created because permission was denied. No retry or alternate archive was attempted.
2. There is no Git commit to restore to, and existing project files are untracked.
3. ComfyUI does not respond on the observed local endpoint; Wan model/workflow presence was not established.
4. No full render, frontend, migration, security, or restore evidence exists in the inspected repository.
