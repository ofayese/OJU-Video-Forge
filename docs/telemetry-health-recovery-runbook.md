# OJU Video Forge — Telemetry Health and Recovery Runbook

**Branch:** `feat/silent-recording-training`
**Applies to:** ERE development host

---

## Telemetry Architecture

- **Publisher:** Windows Scheduled Task "OJU Host Telemetry Publisher"
  - Runs under `ERE\akoso` (Limited, no dedicated service account)
  - Triggers: AtLogOn + AtStartup (60s delay)
  - Daemon enforces 10-second publication cadence internally
  - Reads HMAC key from protected file, signs observations
- **Consumer:** API/worker containers read signed observation via read-only mount
- **State:** `/opt/forge/telemetry-state/state.json` (writable, persistent)
- **Observation:** `C:\ProgramData\OJU Video Forge\telemetry\observation.json` (read-only mount)
- **Admission:** `preflight_heavy_inference()` validates RAM, GPU, HMAC integrity, freshness

---

## Health Check (verify_dev_stack.py)

The `scripts/verify_dev_stack.py` script checks:

1. Compose overlay merges (base, integration, ollama)
2. Network membership (api: backend/control/agent-inference; worker: control/inference/agent-inference)
3. Image versions (matching expected identities)
4. Ollama model endpoints and digests (no host fallback)
5. Secret mounts (HMAC key file exists, read-only)
6. Telemetry publisher (fresh observations, integrity verified)
7. TTS and FFmpeg availability
8. Non-root execution (uid=10001) and read-only rootfs

Run: `python scripts/verify_dev_stack.py`

---

## Publisher Liveness vs. Task State

**Distinguish an intentionally long-running daemon from an actual failure:**

- Task Scheduler "State" may show "Running" for a daemon that launched once and stays alive
- Use observation freshness (collected_at vs. now, max 30s) as the real health signal
- Use process liveness (publisher process actually running) as secondary signal
- Do not rely on task result code alone — the daemon runs indefinitely

---

## Recovery Procedures

### Publisher stopped

1. Check Task Scheduler: `Get-ScheduledTask -TaskName 'OJU Host Telemetry Publisher'`
2. If not running: `Start-ScheduledTask -TaskName 'OJU Host Telemetry Publisher'`
3. Verify fresh observation: check `observation.json` collected_at is within 30s
4. Verify API can read telemetry: `verify_dev_stack.py`

### Stale observations (>30s)

1. Check publisher process is running
2. Check observation directory is writable by publisher
3. Check HMAC key file is accessible
4. Restart publisher task if needed

### HMAC integrity failure

1. Verify key file matches between publisher and consumers
2. Check key file hasn't been corrupted
3. Rotate key if necessary (see dev-secrets-rotation-runbook.md)

### Insufficient RAM/GPU

1. `preflight_heavy_inference()` raises `inference_insufficient_host_ram` or `inference_insufficient_gpu_capacity`
2. Heavy inference is blocked (fail-closed) until resources recover
3. No operator action needed — admission re-evaluates on each request
