# OJU Video Forge — Deployment Runbook (Gate 6)

**Target:** Controlled deployment of candidate API and render-worker images
**Branch:** `feat/silent-recording-training`
**Status:** PLAN ONLY — do not perform deployment without explicit owner approval

---

## 0. Configuration modes

### ERE development configuration

| Element | Value |
|---------|-------|
| Execution identity | `ERE\akoso` (existing account, local Administrators) |
| HMAC key | Local protected file: `C:\ProgramData\OJU Video Forge\secrets\hmac.key` |
| Task scheduler | Windows scheduled task (`OJU Host Telemetry Publisher`) |
| Runtime | Local Docker Desktop |
| Secret management | Local file ACLs (no remote secret store) |
| Orchestration | Docker Compose on localhost |

### Future production configuration

| Element | Value |
|---------|-------|
| Execution identity | Deployment-specific identity (not `ERE\akoso`) |
| HMAC key | Target-host secret management (not a local file) |
| Task scheduler | Production workload orchestration (not Windows Task Scheduler) |
| Runtime | Target-specific (not Docker Desktop) |
| Secret management | Target-specific security and compliance controls |
| Orchestration | Target-specific (not Docker Compose on localhost) |

The final hosting platform is not prescribed by this document. The ERE development configuration uses only the existing `akoso` account and local infrastructure; migration to production requires re-keying identity, secret management, and orchestration without rewriting the core application.

---

## 1. Preflight and job-idleness checks

Before any deployment, verify:

1. **Git state:** Working tree clean on `feat/silent-recording-training`; HEAD matches intended commit
2. **Job idleness:** Redis `rq:failed:gpu` zcard == 0 and `rq:queue:gpu` has no pending jobs; no active RQ workers processing
3. **Live services:** All current containers healthy (`docker ps` — all `Up`/`healthy`)
4. **Ollama container:** `oju-video-forge-ollama-1` running; Qwen3-VL model present (`qwen3-vl:4b`)
5. **Host Ollama:** ERE host `ollama.exe` instance unchanged; not modified by deployment
6. **Volume inventory:** `telemetry-state`, `telemetry-state-lease`, `telemetry-state-test` volumes documented
7. **Resource headroom:** Docker host has sufficient RAM/CPU for rolling recreation
8. **Candidate images:** Both candidate images built and scanned; digests recorded (Section 2)

**Stop condition:** Any preflight check fails → abort deployment.

---

## 2. Immutable candidate image identities

| Service | Image | Tag | Digest |
|---------|-------|-----|--------|
| API | `oju-video-forge/api` | `candidate-telemetry` | `sha256:66a33f0713d30b113e979323a6fd3a9e8b771f418068813b4a0d929b2d3b68d6` |
| Render worker | `oju-video-forge/worker` | `candidate-telemetry` | `sha256:ca813cc4702c7c328227fb90277c40a0483daff6d33a09e7275b925623c55515` |

**Preserved digests (rollback targets):**

| Service | Image | Tag | Digest |
|---------|-------|-----|--------|
| API (current live) | `oju-video-forge-api` | `latest` | `sha256:cf05c5d5b27b` |
| Worker (current live) | `oju-video-forge-render-worker` | `latest` | `sha256:5910206f5a76` |

Images are immutable (digest-pinned); no tag mutation after promotion.

---

## 3. Required runtime secrets and mounts

### Secrets

| Secret | Source | Destination | Notes |
|--------|--------|-------------|-------|
| `INFERENCE_TELEMETRY_HMAC_KEY_FILE` | ERE host key file path | Container env var (file path only) | ERE dev: `C:\ProgramData\OJU Video Forge\secrets\hmac.key` → `/run/secrets/telemetry-hmac.key:ro`. Production: target-host secret management. Never the key value. |
| `DB_PASSWORD` | `.env` | Container env var | Existing |
| `FORGE_OPENWEBUI_TOKEN` | `.env` | Container env var | Existing |
| `FORGE_CHATGPT_TOKEN` | `.env` | Container env var | Existing |

### Mounts

| Mount | Source | Target | Mode | Purpose |
|-------|--------|--------|------|---------|
| `telemetry-state` | Named volume | `/opt/forge/telemetry-state/` | rw | Replay-state + lock files |
| `artifacts` | Named volume | `/artifacts` | rw | Job artifacts |
| `uploads` | Named volume | `/artifacts/projects` | rw | User uploads |
| `config/workflows` | Bind mount | `/workflows` | ro | Worker workflow definitions |
| `secrets/hmac.key` | Bind mount | `/run/secrets/telemetry-hmac.key` | ro | HMAC verification key (single file, not directory) |
| `oju-video-forge_ollama-models` | Named volume | `/models` | ro | Ollama model storage (ollama container only) |

### Read-only rootfs

Both API and worker run with `read_only: true`; writable paths limited to `/tmp` (tmpfs), `/artifacts`, `/artifacts/projects`, `/opt/forge/telemetry-state/`.

---

## 4. Persistent telemetry activation

**NOT activated by this runbook.** Telemetry publisher is a Windows scheduled task on ERE host (see `docs/operations/TELEMETRY-PUBLISHER-DEPLOYMENT-PLAN.md`).

The task runs under the existing `ERE\akoso` account (no `ovf-telemetry` service account created).

Deployment prerequisites for telemetry:

- [ ] ERE host `observation.json` file exists at the path referenced by `INFERENCE_TELEMETRY_FILE`
- [ ] HMAC key deployed to ERE host at `C:\ProgramData\OJU Video Forge\secrets\hmac.key` with correct ACLs
- [ ] Candidate services configured with `INFERENCE_TELEMETRY_STATE_FILE`, `INFERENCE_TELEMETRY_HMAC_KEY_FILE`, `INFERENCE_TELEMETRY_MAX_AGE_SECONDS=30`, `INFERENCE_TELEMETRY_CLOCK_SKEW_SECONDS=5`
- [ ] `telemetry-state` named volume created and accessible
- [ ] Publisher sequence state preserved (`{"last_sequence":8}`); consumer replay state preserved (`{"ere-host":{"sequence":8}}`)

**Activation:** Requires separate owner approval (see Gate 4 plan, Section 14).

---

## 5. API/worker scoped recreation

Deployment sequence (rolling, one service at a time):

1. **Stop render-worker:** `docker stop oju-video-forge-render-worker-1`
2. **Remove render-worker container:** `docker rm oju-video-forge-render-worker-1`
3. **Start render-worker:** `docker run -d ... oju-video-forge-render-worker:candidate-telemetry@sha256:e4b77b89c61fbf8251095c78acfccaee1553cad0c4eb359d49c7f50c13756700`
4. **Verify render-worker health:** Health check passes (`app.worker_health`)
5. **Stop API:** `docker stop oju-video-forge-api-1`
6. **Remove API container:** `docker rm oju-video-forge-api-1`
7. **Start API:** `docker run -d ... oju-video-forge-api:candidate-telemetry@sha256:3aacae74118844bdfb8fcd1d62c69915433b55c37eebfe97249416552cbef1fa`
8. **Verify API health:** Health check passes (`/health/ready`)

**Scoped recreation:** Only API and worker containers are recreated. Ollama, Postgres, Redis, ComfyUI, dispatcher, frontend, watchtower, docling-ocr remain unchanged.

---

## 6. Health and smoke checks

After each service recreation:

| Check | Command | Expected |
|-------|---------|----------|
| API health | `curl http://127.0.0.1:8000/health/ready` | HTTP 200 |
| Worker health | `docker exec <worker> python -m app.worker_health` | Exit 0 |
| Redis connectivity | `docker exec <api> python -c "import redis; redis.Redis.from_url(...).ping()"` | `True` |
| Telemetry state writable | `docker exec <api> test -w /opt/forge/telemetry-state/` | Exit 0 |
| Non-root user | `docker exec <api> id` | `uid=10001(forge)` |

---

## 7. Redis and telemetry admission checks

After both services are running:

1. **Redis lease:** Acquire and release a test lease key; verify ownership check
2. **Telemetry HMAC:** Verify a signed telemetry envelope validates with the configured key
3. **Replay state:** Write to state.json, restart container, verify state persists
4. **Admission fail-closed:** Corrupt state.json → API rejects inference requests
5. **Training-text unconfigured:** `get_training_text_adapter()` raises `AgentUnavailable`
6. **Vision model identity:** `AGENT_MODEL` and `NARRATION_VISION_MODEL` both `qwen3-vl:4b` with correct digest

---

## 8. Rollback to preserved image digests

If any smoke check fails:

1. **Stop candidate API:** `docker stop oju-video-forge-api-1 && docker rm oju-video-forge-api-1`
2. **Start preserved API:** `docker run -d ... oju-video-forge-api:latest@sha256:cf05c5d5b27b`
3. **Stop candidate worker:** `docker stop oju-video-forge-render-worker-1 && docker rm oju-video-forge-render-worker-1`
4. **Start preserved worker:** `docker run -d ... oju-video-forge-render-worker:latest@sha256:5910206f5a76`
5. **Re-verify health:** All health checks pass with preserved images

---

## 9. Post-rollback state integrity

After rollback:

1. **Telemetry state:** state.json and lock file intact and readable
2. **Redis state:** No orphaned lease keys from candidate deployment
3. **Job queue:** No jobs lost or corrupted during rollback
4. **Artifacts:** All artifacts accessible and unmodified
5. **Volumes:** All named volumes mounted and accessible

---

## 10. Exact stop conditions

**Abort deployment immediately if:**

- Any preflight check fails (Section 1)
- Redis `rq:queue:gpu` has pending jobs (job idleness check fails)
- Candidate API health check fails after 3 retries
- Candidate worker health check fails after 3 retries
- Telemetry HMAC verification fails
- Corrupt replay state detected after candidate deployment
- Any running job is affected by container recreation
- Ollama container or model is affected in any way

**Rollback trigger:** Any smoke check failure after deployment.

**No rollback trigger:** Informational warnings, non-critical log messages, individual test skips.

---

## 11. Owner approvals required

Before deployment:

- [ ] Candidate image digests approved
- [ ] Telemetry publisher scheduled task approved under `ERE\akoso` (Gate 4)
- [ ] Writing-model acquisition approved (qwen2.5:7b-instruct, digest pin, TRAINING_TEXT_* config)
- [ ] Execution-gateway design approved (Gate 6 ADR-OVF-003)
- [ ] Persistent telemetry task activation approved
- [ ] Render-worker vulnerability remediation approved (if any unresolved findings)
- [ ] No `ovf-telemetry` service account created or authorized

**No deployment proceeds without all approvals.**
