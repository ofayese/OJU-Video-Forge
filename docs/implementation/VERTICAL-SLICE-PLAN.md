# First vertical slice: secure deterministic project rendering

## Outcome and boundaries

Deliver one complete local workflow:

**Authenticated user → project → media upload → persisted job → Redis dispatch →
CPU FFmpeg render → MP4 → FFprobe verification → SHA-256 → authorized retrieval.**

Use current components as starting points: `app/api.py`, `app/auth.py`,
`app/core.py`, `app/rendering.py`, `app/worker.py`, and
[`rendering-contract-v1.md`](../rendering-contract-v1.md). Do not recreate the
standalone timeline validator. No GPU, cloud, public deployment, or migration to
another environment is part of this slice.

## API contract (`/v1`)

- `POST /v1/auth/session`, `GET /v1/auth/session`, `DELETE /v1/auth/session`:
  local account session; no AD/OIDC dependency.
- `POST /v1/projects`, `GET /v1/projects`,
  `GET/PATCH/DELETE /v1/projects/{project_id}`: only members can discover or mutate
  a project; destructive operations are soft/retention-aware.
- `POST /v1/projects/{project_id}/media`: bounded multipart upload; return an opaque
  media ID, size, detected type, and digest, not a filesystem path.
- `GET /v1/projects/{project_id}/media` and
  `GET /v1/projects/{project_id}/media/{media_id}`: owner-scoped metadata.
- `POST /v1/projects/{project_id}/jobs`: versioned timeline and preset, idempotency
  key, persisted job, and asynchronous dispatch.
- `GET /v1/projects/{project_id}/jobs/{job_id}` and
  `POST .../{job_id}/cancel`: authorized status and cooperative cancellation.
- `GET /v1/projects/{project_id}/artifacts/{artifact_id}`: authenticated streaming
  retrieval after project authorization; never accept a raw path.

Retain current liveness and model-list endpoints only where their contracts remain
useful. Generate and validate the OpenAPI contract in CI.

## Identity, data model, and storage

- Add local `User`, `Project`, `ProjectMembership`, `MediaAsset`, `RenderJob`,
  `Artifact`, and append-only `AuditEvent` records with foreign keys, uniqueness,
  timestamps, and explicit lifecycle states.
- Hash passwords with a reviewed password-hashing library; store only opaque,
  revocable, expiring session credentials (hashed at rest). Use CSRF protection if
  browser cookies are used. Never log credentials.
- Enforce membership and role checks in every endpoint/service/worker boundary;
  test cross-user and cross-project denial.
- Store uploads under a configured private root using server-generated IDs, atomic
  writes, size/type limits, and no client-controlled path segments. Reject traversal,
  symlink escapes, unsupported media, and partial uploads.
- Keep storage behind a small interface to allow future local filesystem/backends
  without making cloud storage mandatory.

## Timeline, jobs, and worker

- Accept only timeline schema v1 in the first slice; validate against the existing
  renderer contract. Persist the exact normalized spec and referenced media IDs.
- Persist job and outbox/dispatch intent in one DB transaction, then enqueue with
  idempotent job IDs. A reconciler must recover `dispatch_pending` jobs after restart.
- State transitions: `queued → dispatch_pending → running → verifying →
  awaiting_review`; failures transition to `failed`, cooperative cancellation to
  `cancelled`. Terminal states never restart without an explicit new job.
- Worker rechecks job state, project/media ownership, immutable input paths, and
  resource limits. Limit queue concurrency and CPU/memory/storage; the first slice
  must not reserve GPU capacity.
- Use configured `preview` or `hd` presets from the renderer; no client-supplied
  FFmpeg flags, executable paths, codecs, or arbitrary filter graphs.

## Output manifest and errors

Store immutable artifact metadata: artifact ID, project/job IDs, relative storage
key, media type, byte size, SHA-256, FFprobe format/duration/dimensions/frame rate
and codecs, timeline schema version, renderer and FFmpeg versions, timestamps, and
verification state. Never expose the storage root or ComfyUI internals.

Return stable, versioned error codes for invalid input, unauthorized access, quota
exceeded, dispatch unavailable, render failure, verification failure, and missing
artifact. Keep diagnostics in restricted logs; public API errors must not contain
secrets, host paths, SQL, or untrusted subprocess output.

## Tests and acceptance

- Synthetic fixtures only; generate a small source clip during CPU integration
  tests. Unit tests do not require Docker/GPU.
- Verify account/session expiry and revocation, project/membership CRUD, role matrix,
  cross-project isolation, upload limits/types/path safety, idempotency, job state
  transitions, Redis/worker restart recovery, cancellation, and audit records.
- Render with pinned CPU FFmpeg in isolated tests; verify playable MP4, FFprobe
  metadata, size/duration/preset, stable SHA-256 for pinned tool/input, private
  storage, and authorized retrieval.
- Verify malformed/oversized timeline, missing media, queue outage, worker crash,
  disk full, checksum mismatch, and unauthorized artifact requests fail closed.
- The slice passes only when an authenticated API user can create a project, upload
  synthetic media, submit and observe a job, retrieve the verified MP4 only with
  authorization, and tests pass from a clean local setup.
- Database migration checks run against fresh isolated PostgreSQL only. No shared
  database migration is part of acceptance.

## Independently testable issue sequence

The public backlog is split into linked, reviewable issues. Issues #5–#9 deliver
the first CPU-rendering slice; optional GPU, captions, review, and agent work does
not block that core slice.

1. [#5 Local authentication and project authorization](https://github.com/ofayese/OJU-Video-Forge/issues/5)
2. [#6 Durable database jobs and Redis dispatch](https://github.com/ofayese/OJU-Video-Forge/issues/6)
3. [#7 Deterministic FFmpeg rendering](https://github.com/ofayese/OJU-Video-Forge/issues/7)
4. [#8 Private media upload and authorized artifact retrieval](https://github.com/ofayese/OJU-Video-Forge/issues/8)
5. [#9 React frontend integration](https://github.com/ofayese/OJU-Video-Forge/issues/9)
6. [#10 Optional ComfyUI/GPU generation](https://github.com/ofayese/OJU-Video-Forge/issues/10), after the [rootless-container security fix in #16](https://github.com/ofayese/OJU-Video-Forge/issues/16)
7. [#11 Narration, transcription, and captions](https://github.com/ofayese/OJU-Video-Forge/issues/11)
8. [#12 Review and approval workflows](https://github.com/ofayese/OJU-Video-Forge/issues/12)
9. [#13 Constrained agent integration](https://github.com/ofayese/OJU-Video-Forge/issues/13)
10. [#14 Operational resilience and recovery](https://github.com/ofayese/OJU-Video-Forge/issues/14)
11. [#15 Local release validation](https://github.com/ofayese/OJU-Video-Forge/issues/15)

The issues contain acceptance criteria, test/evidence expectations, milestones, and
dependency links. Never include private environment details or real user media in
the public backlog.
