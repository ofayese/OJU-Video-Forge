# OJU Video Forge — Backup, Restore, and Portability Runbook

**Branch:** `feat/silent-recording-training`
**Applies to:** ERE development host

---

## Backup Inventory

| Component | Location | Backup Method | Critical |
|---|---|---|---|
| PostgreSQL data | `pgdata` Docker volume | `docker volume cp` or `pg_dump` | Yes |
| Redis persistent state | `redisdata` Docker volume (appendonly) | `docker volume cp` | Yes |
| Telemetry replay state | `telemetry-state` Docker volume | `docker volume cp` | Yes |
| Telemetry observations | `C:\ProgramData\OJU Video Forge\telemetry\` | File copy | Yes |
| HMAC signing key | `C:\ProgramData\OJU Video Forge\secrets\hmac.key` | File copy | Yes |
| Application artifacts | `artifacts` Docker volume | `docker volume cp` | Yes |
| Compose configuration | Repository (`compose*.yaml`) | Git | Yes |
| Dev bearer tokens | `.env` (gitignored) | File copy | Yes |

---

## Recovery Order

1. Restore Docker volumes (pgdata, redisdata, telemetry-state, artifacts)
2. Restore HMAC key file to `C:\ProgramData\OJU Video Forge\secrets\hmac.key`
3. Restore telemetry observations to `C:\ProgramData\OJU Video Forge\telemetry\`
4. Restore `.env` with bearer tokens
5. Start containers via `docker compose up -d`
6. Verify with `scripts/verify_dev_stack.py`

**Do not restore over active production-like volumes.** Use isolated resources for rehearsal.

---

## Monotonic Telemetry Replay After Restore

- Telemetry replay state tracks `(sequence, payload_hash)` per source
- After restore, the state file may be older than the latest published observation
- The `_ReplayStore` accepts higher sequences and same-sequence/same-hash re-reads
- Observations with lower sequences are rejected (replay prevention)
- System self-heals as new observations advance the state

---

## Non-Destructive Rehearsal

1. Copy volumes to a temporary location (do not overwrite active volumes)
2. Start a separate compose project with restored volumes
3. Verify `verify_dev_stack.py` passes
4. Stop and remove the test project

---

## ERE Portability Assessment

| Item | ERE-Specific? | Portable? | Notes |
|---|---|---|---|
| Host paths (`C:\ProgramData\OJU Video Forge\...`) | Yes | Configurable | Use relative paths in future deployments |
| Windows Task Scheduler | Yes | No | Linux deployments need systemd/cron alternative |
| Docker Desktop assumptions | Yes | Partially | Compose files are portable; Windows host paths are not |
| GPU device (NVIDIA RTX 5070 Ti) | Yes | Partially | Requires NVIDIA container toolkit on Linux |
| DNS (`ollama` → container IP) | No | Yes | Docker Compose internal DNS |
| Persistent volumes | No | Yes | Docker volumes are portable |
| Authentication config | No | Yes | `.env` template provided |
| Artifact storage | No | Yes | `/artifacts` volume |
| Required CPU/RAM/VRAM | Yes | Partially | Documented in image-provenance-register.md |

**Do not select or deploy the final production host in this phase.**
