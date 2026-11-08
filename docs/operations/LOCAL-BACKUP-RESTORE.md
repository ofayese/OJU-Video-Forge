# Local resilience and backup/restore

Use the [Docker environment runbook](./DOCKER-ENVIRONMENTS.md) for the current
canonical/test project identities, distinct secret files, safe startup, and
owner-approved data reconciliation. Historical results below do not establish
readiness of a newly named canonical project.

This runbook is for a local Compose project using synthetic data. It does not
authorize access to ERE or another shared environment. Keep backups private and
outside the repository; never put credentials, prompts, media, or raw logs in
Git.

## Readiness and recovery

`GET /health/live` reports only that the API process is responsive.
`GET /health/ready` requires PostgreSQL and Redis and returns HTTP 503 when
either local dependency is unavailable. GPU and cloud adapters are explicitly
optional and do not gate readiness. Do not publish either endpoint publicly.

The worker reconciles queued, dispatch-pending, and interrupted running jobs
before accepting work. Existing queued deliveries are left alone. Only the
deterministic `render_test` operation is retried after interruption: its output
directory is validated and reused, and its generated test file is overwritten.
Running `render_test` records are treated as interrupted on startup even if RQ
still retains their old started record; they are re-enqueued once under a
distinct RQ ID. Non-idempotent operations are marked failed for explicit
resubmission. A second render interruption is marked failed rather than
retried indefinitely. Terminal jobs are not selected for automatic recovery.
The supported Compose configuration runs one worker process; do not scale it
until concurrent-worker recovery coordination is implemented. The worker
processes one queue item at a time, and the deterministic test render has a
120-second FFmpeg timeout.

Schema revisions are not applied automatically. In the intended local Compose
project, start its PostgreSQL dependency and apply the migration with
`docker compose run --rm migrate` before starting the updated
worker. The new revision initializes existing jobs with a recovery count of
zero. Do not run this command against a shared database.

## Local resource and log limits

Compose applies CPU, memory, and process limits to API, worker, PostgreSQL, and
Redis. The optional GPU service has a process and CPU cap but no memory cap:
model requirements vary, so size it explicitly before enabling it. Docker's
local `json-file` logs are capped at three 10 MiB files per service (about
30 MiB per container). This is rotation, not a backup or an application audit
retention policy. Do not log prompts or media; worker failures exposed through
job status contain only a generic message.

## Create and verify a local backup

Run from the repository root with the intended Compose project selected. Choose
a private destination with sufficient free space and restrictive permissions;
do not place it under this repository.

```sh
umask 077
mkdir -p "$HOME/forge-backups"
chmod 700 "$HOME/forge-backups"
docker compose exec -T postgres pg_dump -U forge -d forge -Fc \
  > "$HOME/forge-backups/forge-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker run --rm --network none \
  -v "$HOME/forge-backups:/backup:ro" postgres:17.7-alpine3.22 \
  pg_restore --list /backup/forge-YYYYMMDDTHHMMSSZ.dump >/dev/null
sha256sum "$HOME/forge-backups/forge-YYYYMMDDTHHMMSSZ.dump" \
  > "$HOME/forge-backups/forge-YYYYMMDDTHHMMSSZ.dump.sha256"
```

Replace the example timestamp with the created filename. This database dump
does not include Redis queue state, configuration secrets, model files, or
artifacts. Preserve versioned configuration/workflows and artifacts separately
using an approved private destination and verify artifact checksums. Reconstruct
Redis from durable job state; do not copy a live Redis volume as a substitute
for a consistent database backup.

## Synthetic isolated restore drill

Use a unique Compose project name so its PostgreSQL volume and Redis service are
separate from any regular project. Restore only a dump created from synthetic
records; never use a real or shared database dump for this drill.

```sh
RESTORE_PROJECT="forge-restore-$(date -u +%Y%m%d%H%M%S)"
docker compose -p "$RESTORE_PROJECT" up -d --wait postgres redis
docker compose -p "$RESTORE_PROJECT" exec -T postgres \
  pg_restore -U forge -d forge --clean --if-exists --no-owner < synthetic.dump
docker compose -p "$RESTORE_PROJECT" exec -T postgres \
  psql -U forge -d forge -c 'SELECT owner, status FROM jobs ORDER BY id'
docker compose -p "$RESTORE_PROJECT" stop
```

Confirm that the synthetic owner/status records match the source, and verify any
separately restored artifacts by their recorded SHA-256 values. Retain the
isolated containers and volumes until an accountable owner explicitly approves
their cleanup; stopping does not delete them.
Check cross-owner access through the authenticated API with two synthetic test
actors; a database restore alone does not prove authorization.

Retain backups only in the approved private location and follow its retention
policy. No automated backup destination, scheduled backup, or production
recovery objective is configured by this repository.

## Rollback without data deletion

Restore the prior Compose resource/logging values or prior application image and
recreate only the affected services. Keep the `pgdata` and `redisdata` volumes
and artifact directories. The additive recovery-count column can remain when
rolling back application code that does not use it; do not downgrade merely to
roll back a worker. Never use `docker compose down -v` on a project containing
user data. The restore drill retains its containers and volumes pending explicit
cleanup approval.

## Synthetic validation evidence

On 2026-11-06, the following disposable-service check used a dedicated Compose
project and no production data:

```sh
docker compose -p oju-resilience-synthetic-20261106 \
  -f compose.yaml -f compose.dev.yaml up -d postgres redis
```

Both services reached `healthy` with exit code 0 when run as their image users
(`postgres` and `redis`). Stopping and restarting each service returned exit
code 0; an `exec` probe while stopped returned exit code 1. After `docker start`,
`redis-cli ping` returned `PONG` in 214 ms and `pg_isready` reported accepting
connections in 239 ms. These timings are this sandbox's service-level
measurements, not service-level objectives.

A separate synthetic PostgreSQL drill created one synthetic owner/job record,
ran `pg_dump -Fc`, verified the dump with `pg_restore --list`, restored it into
a separate database, and confirmed `synthetic-owner|awaiting_review` was
preserved. All commands exited 0. The `.venv/bin/python -m pytest -q` suite
reported 81 passed, 1 skipped, and one existing Starlette/httpx deprecation
warning; `.venv/bin/ruff check app tests alembic scripts` passed. Offline
`alembic upgrade head --sql` rendered both revisions, including the recovery
counter migration. A separate unit test confirms the API returns 200 for a
matching synthetic owner and 404 for a different actor; it does not bind to the
restored database.

The full API-to-container readiness/recovery path remains unverified here:
published host ports were unavailable, and a disposable worker container could
not resolve Compose service names (`socket.gaierror: [Errno -3] Try again`).
The HTTP readiness behavior is covered with isolated dependency fakes, and the
recovery state machine is covered with SQLite and a fake queue. A live RQ job
crash/restart, full application-schema migration against this disposable
PostgreSQL instance, artifact/media backup and restore, and a real actor/session
flow remain gaps. The synthetic restore used a minimal jobs table and is not
evidence of a full application recovery.
