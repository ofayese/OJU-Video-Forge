# OJU Video Forge — v0.1 experimental scaffold

**NOT PRODUCTION READY.** ADR-OVF-001 remains PROPOSED.

## Included

- Docker Compose API, RQ worker, Redis, PostgreSQL, optional ComfyUI profile.
- Working deterministic FFmpeg `render_test` operation (once infrastructure is deployed).
- Wan 2.2 ComfyUI prompt **submission adapter only**. This does not claim a completed video;
  an administrator must import an approved ComfyUI *API-format* graph and implement an
  output reconciler before Gate D can pass.
- Alembic initial migration and basic contract tests.
- API spec auto-generated at `/openapi.json` after startup.
- Owner-scoped generated-artifact review bridge (contract v1), with immutable
  database evidence and a local bearer-token UI.

## Contributing and validation

The project is local-first and not production ready. See
[CONTRIBUTING.md](CONTRIBUTING.md), [the development workflow](docs/development/DEVELOPMENT-WORKFLOW.md),
[testing strategy](docs/development/TESTING-STRATEGY.md), and
[security policy](SECURITY.md) before making changes. Python and frontend CI run on
pull requests to `main`; no GPU, Azure, or external application deployment is
required by those checks.

## WSL2 setup

Use Windows NVIDIA graphics drivers; do not install Linux NVIDIA kernel drivers in WSL.
Use Docker Desktop WSL2 backend with GPU support. Keep `$FORGE_DATA` inside the Linux
ext4 filesystem, not under `/mnt/c`.

```bash
sudo mkdir -p /srv/video-forge/{artifacts,models,comfy-input,comfy-output}
sudo chown -R 10001:10001 /srv/video-forge/artifacts
# Ensure model directories are writable by the configured ComfyUI image user.
docker network inspect ai-net >/dev/null || docker network create ai-net
cp .env.example .env
# Generate separate DB and bearer secrets; edit .env; chmod 600 .env
docker compose config --quiet
docker compose build api worker
docker compose up -d postgres redis
# The migration runner needs access to the Compose internal network:
docker compose run --rm --no-deps api python -m alembic -c alembic.ini upgrade head
# See note below: migrations files must be in the image before running.
docker compose up -d api worker
```

## Test API from Open WebUI's Docker network

Open WebUI should reach `http://api:8000/openapi.json` if attached to `ai-net`.
The API has no host-published port by design. For isolated manual smoke tests,
use `docker compose exec api` with a Python or curl HTTP client.

## Application URLs and identity

Use the application namespace for browser and API endpoints:

| Purpose | Target URL |
| --- | --- |
| Video Forge web app | `https://video.olutechsys.com` |
| Video Forge API and agent integrations | `https://video-api.olutechsys.com` |

Use `ojuidc.olutech.systems` for Active Directory identities and internal
infrastructure only; it is not the Video Forge application host or an OIDC issuer
URL. AD/OIDC integration remains optional and must be configured against a verified
provider discovery document before use.

Local development continues to use local or Docker-network addresses (for example,
`http://localhost:5173`, `http://localhost:8000`, and `http://api:8000`). The public
HTTPS names are deployment targets only; this scaffold does not configure DNS, TLS,
an external gateway, or public access.

## AI gate

1. Pin approved ComfyUI git revision and NVIDIA-compatible PyTorch image digest.
2. Download official Wan 2.2 TI2V 5B FP16, UMT5 FP8 and Wan 2.2 VAE into model paths.
3. From ComfyUI export an API-format workflow to
   `config/workflows/wan22-t2v-api.json` and map exact node IDs.
4. Add a reconciler that checks `/history/{prompt_id}`, retrieves only allowlisted
   output filenames using ComfyUI `/view`, validates media and archives it.
5. Reject promotion until a full MP4 and evidence bundle is produced.

## Gaps before production

- OAuth/OIDC JWT validation with JWKS, external reverse-proxy hardening and HTTPS.
- True end-user identities (current service tokens distinguish only service clients).
- Dispatch reconciler, idempotency constraint, robust cancellations and limits.
- GPU output reconciliation and checksum/FFprobe verification.
- Tenant/project RBAC, signed artifact download, backup automation and retention.
- Pin and scan all images and dependencies; audit log export and alerting.
- Durable outbox and deployment smoke/negative tests.

## Rollback

Stop API and workers; preserve Postgres volumes and artifacts; restore a verified
Postgres backup and pinned image set. Never delete volumes during emergency rollback.

### Artifact review bridge (#12)

Dependencies #8/#9 remain OPEN in separate WIP PRs and are not integrated here.
There are no new project, media, account, login, retrieval or publishing APIs.
The existing account UI calls unfinished `/auth/*` and `/projects` routes.
Use **Local service-token artifact review**, above that sign-in flow, with a
configured local bearer token and a known job ID. It calls only current `/v1/jobs`
routes, keeps its token in memory (not browser storage), and clears private results
on authorization errors. Use the localhost Vite proxy for local API access.

Every read/history/decision requires `job.owner == actor.name`, including admins.
Usual authenticated read roles may read; only an owning reviewer or administrator
may decide. Existing local service actors already have reviewer roles. This is a
service-account/self-review bridge, **not independent human signoff** and
**not completed project membership integration**.

- `GET /v1/jobs/{id}` adds `review_contract_version: 1`, `review_version`,
  `review_enabled`, `can_review`, and `review_artifact: {id, sha256} | null`.
  The existing artifact field is unchanged; new review metadata/history never
  contains media, prompts, comments or private storage paths.
- `GET /v1/jobs/{id}/reviews` returns ordered immutable decisions (actor, timestamp,
  job ID, opaque artifact ID, actual SHA-256, expected/next version, decision and
  contract version). Reads/history remain available when review is disabled.
  History uses the indexed `(job_id, version)` key with `after_version` (default
  0) and `limit` (default 50, maximum 100); snapshots never join mutable job fields.
- `POST /v1/jobs/{id}/review` accepts only
  `{"decision":"approved","expected_version":0,"expected_artifact":{"id":"<opaque-id>","sha256":"<64-lowercase-hex>"}}`
  (`rejected` is the other decision). Unknown fields and non-integer versions
  are rejected. Only `awaiting_review → approved | rejected` is allowed.
  Terminal decisions cannot repeat, reverse, requeue or publish.
- A database compare-and-swap checks owner/state/version/artifact identity;
  decision, version and evidence commit together. Stale requests return 409 with
  no evidence. `review_version` is a dedicated monotonic **job workflow version**,
  not an audit count: dispatch, worker claims/completion/failure, ORM updates and
  review decisions all increment it. Worker claims and completion are fenced by
  state/version; stale ORM writes also fail optimistic version checks.
  The UI refreshes on 409/503 without retrying a decision, and requires explicit
  acknowledgement of refreshed details before another decision.
- Only the current worker's nonempty generated `render_test/test.mp4`, with a
  persisted opaque identity and SHA-256, is eligible. The API rehashes a bounded
  regular file through no-follow directory descriptors. Missing, changed or
  unsafe artifacts fail closed. ComfyUI submission JSON is never eligible.
  Hashing streams 1 MiB chunks from a private, fixed job-bound file (maximum
  256 MiB), never request-supplied bytes or arbitrary paths. A stored hash is
  **not filesystem immutability**: it verifies bytes sampled at decision time.
  Only the claim winner creates output; API storage is read-only. Operators must
  not replace/mutate generated files after completion or during review; privileged
  filesystem mutation after hashing is not prevented by the DB transaction.
  There is no preview/download in this bridge: verify media through authorized
  local operations before approving.

Apply additive Alembic `0002` on top of `0001` in an approved isolated environment
before running the new API/worker. Existing rows and audits are preserved; old
outputs have null identities/checksums and are not reviewable. Do not infer or
backfill evidence from legacy paths; generate a new job instead. Drain/stop old
API/worker versions before an approved upgrade: mixed old writers do not implement
the version fence.

`REVIEW_WORKFLOW_ENABLED` defaults to `true` (false/unrecognized values disable).
Set it to `false` and restart/recreate the API to stop decisions with 503 after
authentication/ownership/role checks. It **never** approves, unblocks, deletes or
resets jobs/history; workers still stop at awaiting review. Compose passes the
flag and mounts the same configured artifact root read-only in the API.
Settings are cached per API process: changing the environment alone has no effect.
Restarted processes reject new decisions; a request that already passed the flag
check may finish its transaction during graceful shutdown. Disabling is not an
in-flight transaction cancellation mechanism.
Rollback should disable this flag, not roll back the schema: `0002` downgrade
always refuses destructive removal, including downgrade through `0001`.
Database triggers prohibit updating/deleting review rows (and PostgreSQL truncate);
this is append-only evidence, not cryptographic protection against a DB superuser
or filesystem operator. Keep database/storage private and backup evidence.

## ChatGPT schema

`config/chatgpt-actions.openapi.yaml` is a proposed legacy GPT Actions adapter. Current
OpenAI documentation (Nov 2026) announces custom GPT retirement in favor of plugins;
use this only if your existing GPT Actions workflow is still supported. The OpenAPI
server URL is the proposed `https://video-api.olutechsys.com` target; DNS, TLS, and
gateway availability are not verified. A plugin adapter still needs to be implemented
and registered separately.

## Known gaps in this v0.1 scaffold

- No public gateway, TLS, or per-user OAuth. Do NOT publish `api` directly to the internet.
- WAN job submission is an integration stub, not a completed video renderer.
- Rendering only supports a deterministic test clip, not arbitrary multimedia editing.
- DB reconciliation, event hashing, idempotency, cancellation, project review integration,
  quotas, backup/restore and artifact serving are not implemented.
- Local review HTTP/persistence/UI tests supplement static/unit coverage; see
  [testing evidence](docs/development/TESTING-STRATEGY.md). No service/GPU acceptance
  is claimed by these tests.
