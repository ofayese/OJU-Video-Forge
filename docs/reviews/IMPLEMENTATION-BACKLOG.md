# Implementation Backlog

The requested baseline archive was not created because permission was denied, and no alternate archive was attempted. Partial new renderer, ComfyUI adapter, and frontend files were added before write tasks were stopped. The auth/project backend source-edit request was denied. Do not retry denied effects or use alternate write methods. At the initial baseline, there was no Git commit and repository files were untracked.

| Priority | Work item | Dependencies | Acceptance evidence |
|---|---|---|---|
| P0 | Establish authorized recoverable baseline and Git starting point | User/environment permission | Verified local checkpoint and recorded branch/commit/worktree |
| P0 | Local accounts, sessions, role assignments and project membership | PostgreSQL/migration | Password/session/authorization tests, including cross-project denial |
| P0 | Durable job state and restart/reconciliation behavior | PostgreSQL, Redis/RQ | Redis/worker restart tests; terminal jobs never rerun |
| P0 | Deterministic media/timeline/FFmpeg render slice | Auth, project/media storage, FFmpeg | Playable MP4, FFprobe result, SHA-256, authorized retrieval, audit trace |
| P1 | Real frontend project/media/job workflow | Stable API/auth contract | Typecheck, frontend tests, local browser workflow |
| P1 | ComfyUI integration and GPU admission | Reachable compatible ComfyUI, model/workflow | Retrieved, probed and checksummed output registered as artifact |
| P1 | Review/approval and provenance | RBAC, artifact/job model | Role-gated approval/rejection and immutable audit evidence |
| P1 | Local TTS/transcription/captions | Licensed local models | Offline audio/subtitle tests and timeline sync |
| P1 | Backup/restore | Verified local destination | Isolated restore with integrity comparison |
| P2 | Agent API/OpenAPI/typed client | Stable secured API | Contract validation and restricted-scope tests |
| P2 | Observability/security scans/developer experience | Running local services | Captured health, metrics, scans, and reproducible commands |

ARABA, AD, public exposure, Azure provisioning, and cross-environment inference are explicitly out of scope.
