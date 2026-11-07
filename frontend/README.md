# OJU Video Forge frontend

React + TypeScript + Vite client. It makes live requests only and does not include
sample project, media, job, or model records.

## Local development

1. Copy `.env.example` to `.env.local` if you want to set `VITE_API_BASE_URL`
   (for example `http://localhost:8000`). With the value empty, Vite proxies
   `/v1` to `http://127.0.0.1:8000`.
2. Install dependencies with `npm ci`.
3. Run `npm run dev`, `npm run typecheck`, `npm test`, or `npm run build`.

Access tokens remain in React memory only and are sent as bearer credentials.
Requests use `cache: "no-store"`; uploaded media and downloaded artifact bytes are
not written to browser storage. The API must return protected responses with
`Cache-Control: private, no-store`. A 401 clears the in-memory session. A 403 is
shown as an authorization error without treating it as an expired session.

## API contract

The client uses the local `/v1` contract described in
[`../docs/implementation/VERTICAL-SLICE-PLAN.md`](../docs/implementation/VERTICAL-SLICE-PLAN.md):

- `POST /v1/auth/session` accepts `{ "username", "password" }` and returns
  `{ "access_token", "user"?: { "name", "roles"?, "provider"? } }`.
  `GET /v1/auth/session` returns the current user; `DELETE` ends the session.
- `GET /v1/projects` returns an array or `{ "projects": [...] }`; `POST` accepts
  `{ "name" }` and returns a project `{ "id", "name" }`.
- `POST /v1/projects/{project_id}/media` accepts multipart field `file` and returns
  `{ "id", "size_bytes", "media_type", "sha256" }`.
- `POST /v1/projects/{project_id}/jobs` accepts a version-1 timeline with uploaded
  `media_id` references and a `preview` or `hd` preset. The client sends an
  `Idempotency-Key` header. The response includes a job `id` and `status`.
- `GET /v1/projects/{project_id}/jobs/{job_id}` returns job status and, when ready,
  an opaque `artifact_id`. The UI polls active jobs and requests
  `GET /v1/projects/{project_id}/artifacts/{artifact_id}` with the same bearer token
  before offering an in-memory blob URL for download.
- `GET /v1/models` remains available for service capability information.

These are frontend contract expectations, not evidence that the full API is
implemented. In the current backend, only `/v1/models` and global `/v1/jobs` routes
exist; local sessions, projects, project media, scoped jobs, and authorized artifact
retrieval are not implemented yet. Do not treat frontend visibility as authorization
or expose an artifact by constructing a public URL. Validate the session/project/
upload/job/artifact routes against the backend implementation before claiming an
end-to-end workflow.

## Rollback

Keep the previous production frontend bundle available when promoting a new bundle.
If the UI must be reverted, restore that frontend bundle only; leave additive API
routes and persisted data untouched. Do not roll back or delete database volumes or
private media as part of a frontend rollback. The previous bundle remains usable
only while its API contract is still supported.
