# OJU Video Forge frontend

Docker-first React/TypeScript/Vite client using live APIs, without sample records.

## Docker development and checks

Run from the repository root; no host Node/npm installation is needed:

```powershell
docker build --target test -t ovf-frontend:test frontend
docker build --target dev -t ovf-frontend:dev frontend
docker build --target production -t ovf-frontend:production frontend
```

The test target runs `npm ci` in the dependencies stage, then typecheck, Vitest,
production build and `npm audit` (no scanner suppression). Node is pinned to
26.1.0 on Alpine 3.23; production uses pinned nginx 1.30.2 and runs as `nginx`,
listening on **8080**, with temporary files in `/tmp`. Build context must be
`frontend/`; `.dockerignore` excludes host dependencies, bundles and private env
files. The `dev` target runs Vite on **0.0.0.0:8080** with a strict port (no
automatic fallback). All Node stages use **`WORKDIR /opt/forge`**, so narrow
development mounts target `/opt/forge/src` and `/opt/forge/index.html`.
Both dev and production support the parent's host/container mapping `5173:8080`.

The orchestration owner must attach the frontend to the API's Docker network,
where the backend is named `api` and listens on port 8000. Vite and nginx proxy
both `/v1` and `/health` to that service. Example standalone runs **after that
network/API is provided** (replace the network placeholder):

```powershell
docker run --rm --network <api-network> -p 127.0.0.1:5173:8080 ovf-frontend:dev
docker run --rm --network <api-network> -p 127.0.0.1:5173:8080 ovf-frontend:production
```

Keep `VITE_API_BASE_URL` empty for same-origin requests. A development-only
explicit API origin may be set via an environment variable on the dev container;
production always uses relative `/v1` requests through nginx and ignores that
origin override. Native browser fetch is called through its global receiver,
not as an API-client method. Vite variables are build-time, not nginx runtime configuration.
Do not bake credentials into images. This frontend change does not start Compose
or change its port/stage configuration; the parent owns orchestration. Use TLS
for any non-loopback use; this development image is not a production-readiness claim.
After rebuilding production, the orchestration owner must replace the running
frontend container and reload any retained browser page to load the new bundle.

## Authenticated v1 contract

- Register: `POST /v1/auth/register` with `{username,password}` returns
  `{id,username}`. Registration is followed by a real session request.
- Login: `POST /v1/auth/session` with the same input returns
  `{access_token,user:{id,username}}`. GET reads the session; DELETE revokes it.
- Projects: `GET /v1/projects` lists owned/authorized projects, POST `{name}`
  creates one. Selecting a different project clears its local timeline/job/preview.
- Media: GET `/v1/projects/{id}/media` returns manifests
  `{id,project_id,filename,content_type,size_bytes,sha256}`; POST sends multipart
  field **`file`**, without setting the multipart Content-Type manually.
- Render: POST `/v1/projects/{id}/jobs` sends `Idempotency-Key` and:

```json
{
  "operation": "render_timeline_v1",
  "timeline": {
    "version": 1,
    "clips": [{"media_id": "uploaded-media-id", "in_ms": 0, "out_ms": 5000}],
    "output": {"profile": "preview", "format": "mp4"}
  }
}
```

Optional timeline fields: `title:{text,start_ms,end_ms}`,
`captions:[{text,start_ms,end_ms}]`, and
`audio:{media_id,in_ms,volume_percent}`. The editor supports ordered/repeated clips,
trim times, title, non-overlapping captions and a separately uploaded WAV/MP3
audio track. Preview clip times use the 24-fps frame grid (multiples of 125 ms
are convenient integer values). UI preflight checks bounds; backend validation,
ownership and media inspection remain authoritative. Source audio is omitted
unless the optional audio track is selected.

GET `/v1/jobs/{id}` polls active jobs. `awaiting_review` means the verified render
is complete, not automatically approved. The `artifact` relative URL is metadata;
the client never navigates to it or trusts it as a fetch destination. It fetches
the fixed `/v1/jobs/{id}/artifact` route with bearer authorization, previews a
memory-only blob URL and offers MP4 download. URLs are revoked on replacement,
explicit clear, project switch, sign-out and unmount. Identical ambiguous render
retries reuse the key; changed payloads and successful submissions use a new key.

Account rendering retains owner/role-scoped review/history with explicit loading.
The separate local service-token review bridge remains available before login.
Review approval is never inferred from render completion.

## Privacy and limitations

Tokens live only in React/controller memory. No local/session storage, cookies or
browser credential storage is used by the client. Requests omit browser
credentials, reject redirects and use `cache: "no-store"`; API responses must also
be private/no-store. 401 clears the matching account session (not another session
because of a late request); 403 is an authorization error, not automatic logout.
Project/session unmount fences late async results. Do not log private media or
credentials.

Container unit/contract tests and SSR presentation checks are not browser E2E,
real rendering, API integration, orchestration, container vulnerability scans
or deployment evidence. The parent must verify matching upload/list/render
routes and authenticated preview/download end-to-end.

## Rollback

Restore the previous frontend bundle only while its versioned API contract is
supported. Do not roll back backend schema or delete private media/data volumes.
