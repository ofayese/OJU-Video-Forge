# Dependency Matrix

| Capability | Prerequisite | Implementation dependency | Validation |
|---|---|---|---|
| Local authentication | PostgreSQL schema/migration; password/session configuration | User, role, session and audit models; login/logout/recovery API | Password, expiry/revocation, brute-force and role tests |
| Project isolation | Authentication principal and membership schema | Project/member APIs and scoped queries | Cross-user/project access-denial tests for projects, assets, jobs and artifacts |
| Media management | Authorized project API; local storage policy | Upload/metadata/storage/checksum service | Size/type/path tests and authorized retrieval |
| Timeline editing | Project/media models and versioned timeline schema | Timeline CRUD, revisions, validation and frontend editor | Schema/operation tests and round-trip persistence |
| Deterministic render | Media storage, timeline schema, FFmpeg/FFprobe, worker | Safe command builder, bounded worker, artifact registration | Render fixture, FFprobe, checksum and API retrieval |
| Job reliability | PostgreSQL, Redis/RQ, worker identity | Durable dispatch/reconciliation, transitions, retry/cancel/idempotency | Redis outage/restart, worker restart and duplicate tests |
| ComfyUI video | GPU compatibility, reachable ComfyUI, compatible model and workflow | Registry, admission control, polling/retrieval, verification, provenance | Actual output file, media probe, checksum and registered artifact |
| Narration/captions | Local models and validated licenses | Audio job types, subtitle storage/timeline synchronization | Offline TTS/transcription fixture and SRT/VTT checks |
| Review workflow | Auth/RBAC, artifact store, audit trail | Approval records and decision APIs/UI | Role-gated approve/reject and audit tests |
| Frontend workflows | Stable API/auth contract and real endpoints | React/TypeScript client and UI | Component/API/E2E tests against local services |
| Agent API | Stable versioned API and restricted service identities | OpenAPI contract and typed client/adapters | Local contract and scope tests |
| Backup/restore | Actual storage destination; database/object inventories | Backup, manifest/checksum, restore tooling | Isolated restoration and data-integrity comparison |
| Release readiness | All above and observed ERE deployment configuration | Documentation, gates, operations procedures | Reproduction commands and evidence per gate |

The current baseline has not validated PostgreSQL migrations, Redis recovery, FFmpeg output, ComfyUI execution, frontend behavior, or backup restoration. Local ComfyUI was unavailable at the observed endpoint.
