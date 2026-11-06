# OJU Video Forge frontend

React + TypeScript + Vite client. It makes live requests only and does not include
sample project/job/model records.

## Local development

1. Copy `.env.example` to `.env.local` if you want to set `VITE_API_BASE_URL`
   (for example `http://localhost:8000`). With the value empty, Vite proxies
   `/auth`, `/projects`, and `/v1` to `http://127.0.0.1:8000`.
2. Install dependencies from `package.json` in an approved/offline environment.
3. Run `npm run dev`, `npm run typecheck`, `npm test`, or `npm run build`.

The bearer token is kept in `sessionStorage` for the current browser tab. A 401
clears the local session; sign-out clears it even when the API logout request fails.

## API contract assumptions and baseline gaps

- Login sends `POST /auth/login` with `{ "username", "password" }` and expects
  `{ "access_token", "user"?: { "name", "roles"?, "provider"? } }`.
- `GET /auth/me` returns `{ "name", "roles"?, "provider"? }`;
  `POST /auth/logout` accepts the bearer token and can return an empty response.
- `GET /projects` returns an array or `{ "projects": [...] }`; `POST /projects`
  accepts `{ "name" }` and returns a project `{ "id", "name" }`.
- Existing API code exposes `GET /v1/models` and per-ID `GET /v1/jobs/{job_id}`.
  It does **not** expose a job-collection/list route, so the UI queries an entered
  job ID rather than pretending a recent-jobs list exists.
- At repository baseline, `app/api.py` implements neither `/auth/*` nor `/projects`.
  The login/project calls therefore require the corresponding backend API contract
  to be implemented; frontend behavior against those routes is not integration-tested.
- `GET /v1/models` currently returns the candidate `wan22-ti2v-5b` with
  `requires_workflow_registration`; the UI displays that service response as-is and
  does not imply the model can currently generate media.
