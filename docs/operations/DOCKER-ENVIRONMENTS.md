# Docker project identity and safe reconciliation

## Environment contract

Requires Docker Desktop Linux containers and Compose 2.24.4+ (`!override` ports).
Python, Node, FFmpeg, PostgreSQL, Redis, and application tests run in containers.

| Environment | Compose name | Files | Private secrets | Frontend |
|---|---|---|---|---|
| Main local application | `oju-video-forge` | `compose.yaml` | `.env` | `127.0.0.1:5173` |
| Acceptance | `oju-video-forge-acceptance` | base + `compose.acceptance.yaml` | `.env.acceptance.local` | `127.0.0.1:5174` |
| Integration | `oju-video-forge-integration` | base + `compose.integration.yaml` | `.env.integration.local` | `127.0.0.1:5175` |

These overlays extend the existing stack; they are not independent deployments.
Do not combine test overlays with `compose.dev.yaml`, which publishes a development
API port. The optional GPU overlay is unrelated to CPU acceptance.

Each project gets its own `<project>_{pgdata,redisdata,uploads,artifacts}` named
volumes and `<project>_{backend,control,inference}` networks. GPU models/input/output
volumes are also project-scoped. No volumes or networks are external, and no
`container_name` is specified. Expected names are `<project>-<service>-1`; services
remain `frontend`, `api`, `postgres`, `redis`, `dispatcher`, `render-worker`,
`migrate`, and optional `comfyui`. Default locally built image names are
`<project>-<service>`; infrastructure images are pinned in the base configuration.

The frontend proxies `/v1` and `/health` to its own `api:8000`. API/worker/dispatcher
use `postgres:5432` and `redis:6379` on their own private network. PostgreSQL, Redis,
and ComfyUI have no published ports. A frontend must never join another project's
backend network or use another project's API address.

## Optional Wan GPU service

The GPU overlay enables the private ComfyUI service and routes generation jobs to
the existing Redis-backed `gpu` queue. Model weights stay in the
`oju-video-forge_models` named volume; never copy them into the repository or
publish ComfyUI's port. These commands apply only to the canonical local project and
do not start or recreate PostgreSQL, Redis, or unrelated services.

PowerShell, after the approved model assets are installed:

```powershell
$env:COMFYUI_ENABLED = "true"
docker compose --project-name oju-video-forge --env-file .env -f compose.yaml -f compose.gpu.yaml --profile gpu build api render-worker
docker compose --project-name oju-video-forge --env-file .env -f compose.yaml -f compose.gpu.yaml --profile gpu up -d --no-deps api render-worker comfyui
```

To return API and worker to CPU-only operation, stop ComfyUI and reapply the base
configuration. This preserves all named volumes:

```powershell
docker compose --project-name oju-video-forge --env-file .env -f compose.yaml -f compose.gpu.yaml --profile gpu stop comfyui
$env:COMFYUI_ENABLED = "false"
docker compose --project-name oju-video-forge --env-file .env -f compose.yaml up -d --no-deps api render-worker
```

Never use `down -v` to disable GPU generation.

## Overrides and safeguards

Compose project-name precedence is `--project-name`/`-p`, then
`COMPOSE_PROJECT_NAME`, then the last configuration's `name`, then directory
defaults. `COMPOSE_FILE`, `COMPOSE_PROFILES`, `--env-file`, extra `-f` files, explicit
volume names, and shell `DB_PASSWORD` values can also change effective behavior.
Check shell overrides before starting anything; do not print other secret values.

The PowerShell/Bash helpers explicitly select the project, files, and secret file,
reject naming/storage overrides and destructive operations, and require distinct,
URL-safe random database secrets (32+ characters) across environments. Private
environment files are Git-ignored. Do not use placeholder values for startup.
Helpers never rename volumes, migrate existing data, remove containers, or delete
volumes. Safe shutdown is **stop**, not volume deletion.

Acceptance additionally verifies actual container project labels, mounted volume
names, network namespaces, and absence of writable host mounts before writing
synthetic accounts/media. The smoke script refuses non-test application settings.
Keep any environment-switch approval separate from the startup command.

## Commands after owner approval

Create the corresponding private environment file from `.env.development.example`;
replace its database password with a unique URL-safe random secret. Do not hardcode
administrator credentials or reuse another environment's secrets.

PowerShell:

```powershell
.\scripts\forge.ps1 -Environment application -Plan
.\scripts\forge.ps1 -Environment application build
.\scripts\forge.ps1 -Environment application up -d --wait
.\scripts\forge.ps1 -Environment application ps
.\scripts\forge.ps1 -Environment application logs --tail 50
.\scripts\forge.ps1 -Environment application exec -T api python -m app.worker_health
.\scripts\forge.ps1 -Environment application stop

.\scripts\forge.ps1 -Environment acceptance build
.\scripts\forge.ps1 -Environment acceptance up -d --wait
.\scripts\acceptance.ps1 -Environment acceptance
.\scripts\forge.ps1 -Environment acceptance stop
```

`migrate` explicitly runs `python -m alembic upgrade head`, after database health
and before dependent services start. It must exit 0; PostgreSQL health alone is
not schema migration evidence. Repeat migrations only on the selected database.
The optional bootstrap command prompts privately and refuses a second administrator:

```powershell
.\scripts\forge.ps1 -Environment application exec api python -m app.accounts bootstrap YOUR_USERNAME
```

Bash equivalents:

```sh
bash scripts/forge.sh application build
bash scripts/forge.sh application up -d --wait
bash scripts/forge.sh application ps
bash scripts/forge.sh application stop
bash scripts/forge.sh acceptance up -d --wait
bash scripts/forge.sh acceptance run --rm --no-deps --entrypoint python api -m scripts.docker_render_smoke
bash scripts/forge.sh acceptance stop
```

Use the PowerShell acceptance helper for the additional Docker-label/storage
preflight. For a Bash acceptance run, inspect the selected project's container
labels, mounts, and network names first; never run synthetic writes on canonical
application storage.

## Data-preserving reconciliation

1. Inventory containers, Compose labels, image IDs, health, ports, networks, and
   every volume/bind mount. Identify one-off diagnostics from their command,
   labels, and active process; do not classify them from names alone.
2. Inspect aggregate user/project/job/media counts. Do not publish usernames,
   password hashes, sessions, tokens, private storage paths, or raw database dumps.
3. Obtain the owner's data disposition: fresh canonical database, selective
   migration, or hold. Never automatically transfer synthetic test users.
4. Back up before stopping/replacing services. For a running database use
   containerized `pg_dump -Fc`, verify with containerized `pg_restore --list`, and
   retain checksums. Back up media/artifacts separately with private permissions.
5. A cleanly stopped PostgreSQL volume may be archived read-only as an offline
   physical snapshot. Verify `pg_controldata` reports `shut down`, archive readability,
   and checksum. This is **not** a logical migration or a tested restore.
6. For migration, restore a logical dump into a new isolated, compatible PostgreSQL
   instance; verify schema, aggregate records, authentication, artifact hashes,
   and authorization. Transfer media while writers are quiesced and verify hashes.
   Never rename Docker volumes or copy live PostgreSQL files between instances.
7. Confirm port ownership, then obtain approval to stop the identified legacy
   project. Retain its containers and volumes until explicit cleanup approval.
8. Start canonical services only after approved disposition/backups. Confirm the
   5173 listener has canonical Compose labels and uses its own API/database. Run
   dedicated account registration/login and authenticated render tests.

Database and media volumes survive container recreation. Redis AOF is queue
persistence, not the authoritative job ledger. PostgreSQL/outbox records govern
reconciliation. API/worker scratch files use bounded `/tmp` tmpfs; renderer scratch
files and staged input copies are removed at terminal states, while original
uploads and verified outputs remain in their respective named volumes.

Rollback: stop the affected project; preserve every volume and private backup.
Rebuild/reuse the prior compatible images after owner approval. Prefer leaving
additive schema columns in place rather than downgrading blindly.

## Current reconciliation evidence

See [the reconciliation report](../reviews/DOCKER-NAMING-RECONCILIATION.md).
The report records the ERE runtime, private-backup restore verification, and
end-to-end CPU render evidence. This host-specific snapshot is not a guarantee of
readiness on another machine or a substitute for rerunning the documented checks.
