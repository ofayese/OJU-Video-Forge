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
sudo mkdir -p /srv/video-forge/artifacts
sudo chown -R 10001:10001 /srv/video-forge/artifacts
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

The optional `gpu` profile runs ComfyUI as UID/GID `10001:10001`, with all
Linux capabilities dropped and no published port. Before enabling it, inspect
`${FORGE_DATA:-/srv/video-forge}/{models,comfy-input,comfy-output}` and arrange
for UID 10001 to traverse the parent directories, read model files, and write
to the input and output directories. ComfyUI may also need to create model
subdirectories on startup, so the models directory must be writable. For new,
empty directories an administrator can create them with:

```bash
sudo install -d -o 10001 -g 10001 -m 750 \
  "${FORGE_DATA:-/srv/video-forge}/models" \
  "${FORGE_DATA:-/srv/video-forge}/comfy-input" \
  "${FORGE_DATA:-/srv/video-forge}/comfy-output"
```

For existing data, inspect ownership and permissions first; grant only the
necessary access to the relevant directories and model files using targeted
ownership, group permissions, or ACLs. Do not recursively change ownership or
permissions of existing data. Account for user-namespace remapping if enabled.
Disable the GPU profile to roll back; preserve existing models and media.
Temporary files and previews are stored in the container's writable home and
are not preserved when the container is removed.

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
- DB reconciliation, event hashing, idempotency, cancellation, review workflows,
  quotas, backup/restore and artifact serving are not implemented.
- Test coverage is static/unit only; no Docker or GPU test was executed here.
