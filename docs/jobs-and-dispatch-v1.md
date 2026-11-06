# Job persistence and dispatch contract v1

PostgreSQL is the source of truth for projects, media metadata, jobs, audit events,
and dispatch intent. Media bytes are not stored in these tables. Each accepted job
and its `dispatch_outbox` row are committed in the same transaction before Redis is
contacted.

## Job states

| State | Meaning | Allowed next states |
|---|---|---|
| `dispatch_pending` | Durable intent exists and is waiting for Redis delivery or retry. | `queued`, `running`, `cancelled` |
| `queued` | Redis accepted the stable RQ job ID. | `running`, `cancelled` |
| `running` | A worker acquired the job. | `cancel_requested`, `awaiting_review`, `submitted_to_comfyui`, `failed` |
| `cancel_requested` | Cancellation was requested while work was active. | `cancelled`, `failed` |
| `awaiting_review` | A CPU render finished. | Terminal |
| `submitted_to_comfyui` | The approved workflow was submitted. | Terminal |
| `failed` | Work failed or recovery found a worker lost beyond its timeout. | Terminal; retry creates a new job linked by `retry_of_job_id`. |
| `cancelled` | Work was cancelled before dispatch or cooperatively by a worker. | Terminal |

Job transition rules are enforced in the application and by the database status
constraint. Audit events record dispatch failures, transitions, retries, and
recovery. Reusing an `Idempotency-Key` for an actor returns the original job only
when its project, media, and normalized request match; a different request receives
HTTP 409.

## Durable delivery and recovery

The outbox has `pending`, `dispatching`, `dispatched`, and `cancelled` states.
Dispatch claims are leased for 60 seconds. Redis receives the immutable application
job ID as its RQ ID; concurrent or restarted dispatchers recognize an existing RQ
job instead of creating another. Failed Redis calls are retained as pending with a
bounded exponential delay, attempt count, and exception-class diagnostic. The
`dispatch_error` and `dispatch_attempts` fields are visible on job responses.

The API attempts an immediate dispatch after the database commit. The private
Compose `dispatcher` process retries pending and expired claims every second, so
API failure or dispatcher restart does not lose committed work. A job that remains
`running` past the RQ timeout plus a five-minute grace period is marked `failed`
with `worker_lost`. A retry of such a failure creates a new job and preserves the
original terminal record.

Cancellation before dispatch closes the outbox and marks the job cancelled.
Cancellation during FFmpeg execution is checked while the bounded subprocess runs.
For ComfyUI work the worker checks before submission and after the submission
response; cancelling a remote generation already accepted by ComfyUI is not
supported by this contract.

## Migration and worker rollout

Revision `0002` adds nullable project/media/retry references and dispatch diagnostics
to existing jobs, plus the project, media, and outbox tables. Revision `0001`
remains unchanged. Apply migrations to the local Compose database or an isolated
test database only.

Roll out in this order: apply `0002`, start workers that understand both `queued`
and `dispatch_pending`, then deploy the API and dispatcher. This worker accepts
both states, so existing queue entries remain valid. The prior worker also accepts
both initial states. During rollback, stop new API submissions and the dispatcher,
allow active work to settle, and take a verified database backup. Downgrading
removes the added project/media/outbox schema and job metadata; preserve any new
project/media data before downgrade. Existing `0001` job and audit rows are not
deleted by the migration.

The Compose database, Redis, worker, and dispatcher remain on the private `control`
network. Integration tests require `APP_ENV=test`, a disposable PostgreSQL database
whose name ends in `_test`, and Redis database 15; they refuse other targets.
