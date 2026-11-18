# OJU Video Forge — Host Telemetry Publisher: Scheduled-Task Deployment Plan (Gate 4)

**Target host:** ERE (Windows 11, `CSName=ERE`)
**Component:** `scripts/collect-host-telemetry.ps1` + `scripts/publish-host-telemetry.py` + `app/host_telemetry.py`
**Status:** DESIGN ONLY — not registered, not activated, requires explicit owner approval

---

## 1. Dedicated execution identity

The scheduled task runs under a dedicated, non-interactive service account:

- **Account:** `ERE\ovf-telemetry` (new local account, created by the install script)
- **Logon type:** Service (`SERVICE_LOGON_TYPE`), not interactive
- **Group membership:** None beyond `Users`; removed from `Administrators`
- **Privileges:** `SeServiceLogonRight` only; `SeBatchLogonRight` revoked after task creation
- **Password:** 32-character random, stored in Windows Credential Manager, rotated per key rotation schedule
- **Justification:** Isolation from the interactive `akoso` account and from any other scheduled tasks; least-privilege execution

---

## 2. Protected HMAC signing key — separated from observation directory

- **Key file:** `C:\ProgramData\OJU Video Forge\secrets\hmac.key` (32 random bytes)
- **Key directory:** `C:\ProgramData\OJU Video Forge\secrets\` — NOT the observation directory
- **Key ACL:** `SYSTEM:(F)`, `ERE\ovf-telemetry:(R)`, `BUILTIN\Administrators:(F)`; inheritance disabled
- **Observation directory ACL:** `C:\ProgramData\OJU Video Forge\telemetry\` — `ERE\ovf-telemetry:(RW)` for output only; key file NOT present here
- **Encryption:** EFS-encrypted or DPAPI-protected at rest (machine-level)
- **Delivery:** Out-of-band via secure channel; never in task definition, never in command-line arguments, never in environment variables
- **Publisher reads key:** `INFERENCE_TELEMETRY_HMAC_KEY_FILE` environment variable set to the key file path
- **Consumers receive key:** Independent read-only bind mount `C:/ProgramData/OJU Video Forge/secrets/hmac.key` → `/run/secrets/telemetry-hmac.key:ro`. Consumers see only this single key file, never the secrets directory.
- **Verification:** `read_trusted_telemetry()` in `app/inference_coordination.py` verifies HMAC using the key read from `INFERENCE_TELEMETRY_HMAC_KEY_FILE`

---

## 3. Collection interval — 10 seconds

- **Interval:** 10 seconds
- **Freshness limit:** `INFERENCE_TELEMETRY_MAX_AGE_SECONDS=30` (configured in candidate services)
- **Justification:** 10s < 30s freshness limit, providing at least three observations within the freshness window; accommodates clock skew of up to 5s (`INFERENCE_TELEMETRY_CLOCK_SKEW_SECONDS=5`) and collection time
- **Publisher cadence:** `scripts/publish-host-telemetry.py --daemon` publishes every 10 seconds (`PUBLISH_INTERVAL_SECONDS=10`)
- **Schedule trigger:** Task Scheduler daily trigger at boot with 10-second repetition, indefinitely

---

## 4. Overlap prevention

- **Publisher lock:** `scripts/publish-host-telemetry.py` acquires an exclusive file lock on `<output_dir>/publisher.lock` before each publication cycle
- **Overlap behavior:** If another instance holds the lock, the new instance exits immediately (exit code 3) rather than waiting or queuing
- **Lock lifetime:** Bound to the publisher process; released on exit or crash
- **Daemon mode:** Single long-running process with internal 10-second sleep loop; no overlapping executions possible

---

## 5. Retry and failure policy

- **Retry behavior:** Daemon mode catches and logs errors, continues to next 10-second interval; automatic recovery after transient failures
- **Reboot recovery:** Task Scheduler boot trigger starts the daemon after network and time sync (60s delay)
- **Error handling:** Collector exits non-zero on failure; error logged; no silent fallback
- **No backoff storm:** Fixed 10s interval, not exponential

---

## 6. Task startup behavior

- **Start trigger:** At system boot (delay 60s to allow network and time sync)
- **Run mode:** Daemon (`--daemon`) runs indefinitely until stopped
- **Stop if running longer than:** 5 minutes (prevents hung collector; daemon restarts at next boot trigger)
- **Start only if:** Network is available (to allow future remote endpoints, though current design is local-only)
- **Wake to run:** Disabled (no wake timers)
- **Hidden:** True (no UI)

---

## 7. Minimum privileges

- **Task rights:** `SeServiceLogonRight` for `ERE\ovf-telemetry` only
- **File system:**
  - Collector script (`scripts/collect-host-telemetry.ps1`): `ERE\ovf-telemetry:(RX)`, `SYSTEM:(F)`, `BUILTIN\Administrators:(F)`
  - Publisher script (`scripts/publish-host-telemetry.py`): `ERE\ovf-telemetry:(RX)`, `SYSTEM:(F)`, `BUILTIN\Administrators:(F)`
  - Output directory (`C:\ProgramData\OJU Video Forge\telemetry\`): `ERE\ovf-telemetry:(RW)` (for observation.json, publisher sequence state, lock file), `SYSTEM:(F)`, `BUILTIN\Administrators:(F)`
  - Secrets directory (`C:\ProgramData\OJU Video Forge\secrets\`): `ERE\ovf-telemetry:(R)` for key file only; consumers cannot browse this directory
  - HMAC key: `ERE\ovf-telemetry:(R)` only
- **Registry:** No write access required
- **Network:** No outbound network required (local NMI/WMI/nvidia-smi calls only)
- **Elevation:** Does not require `SeDebugPrivilege` or `SeTcbPrivilege`

---

## 8. Atomic publication

The publisher writes telemetry atomically:

1. Collect RAM (GlobalMemoryStatusEx) and GPU capacity (nvidia-smi) into a temporary structure
2. Serialize to canonical JSON (sorted keys, fixed separators)
3. Compute HMAC-SHA256 over the canonical bytes using the signing key
4. Write envelope `{payload, signature}` to a temporary file in the observation directory
5. Atomic rename (temp → observation.json) on NTFS
6. Update publisher sequence file (atomic via rename)
7. Apply file protection ACLs to the output file

The `apply_file_protection` function in `app/host_telemetry.py` applies ACLs to the output file after publication.

---

## 9. Protected ACLs

| Path | Owner | ACL |
|------|-------|-----|
| `C:\ProgramData\OJU Video Forge\secrets\hmac.key` | SYSTEM | `SYSTEM:(F)`, `ERE\ovf-telemetry:(R)`, `BUILTIN\Administrators:(F)` |
| `C:\ProgramData\OJU Video Forge\telemetry\observation.json` | SYSTEM | `SYSTEM:(F)`, `ERE\ovf-telemetry:(RW)`, `BUILTIN\Administrators:(F)` |
| `C:\ProgramData\OJU Video Forge\telemetry\.telemetry-sequence-state.json` | SYSTEM | `SYSTEM:(F)`, `ERE\ovf-telemetry:(RW)`, `BUILTIN\Administrators:(F)` |
| `C:\ProgramData\OJU Video Forge\telemetry\publisher.lock` | SYSTEM | `SYSTEM:(F)`, `ERE\ovf-telemetry:(RW)`, `BUILTIN\Administrators:(F)` |
| `C:\ProgramData\OJU Video Forge\telemetry-state\state.json` | SYSTEM | `SYSTEM:(F)`, `ERE\ovf-telemetry:(RW)`, `BUILTIN\Administrators:(F)` |
| `C:\ProgramData\OJU Video Forge\telemetry-state\*.lock` | SYSTEM | `SYSTEM:(F)`, `ERE\ovf-telemetry:(RW)`, `BUILTIN\Administrators:(F)` |
| `scripts/collect-host-telemetry.ps1` | SYSTEM | `SYSTEM:(F)`, `ERE\ovf-telemetry:(RX)`, `BUILTIN\Administrators:(F)` |
| `scripts/publish-host-telemetry.py` | SYSTEM | `SYSTEM:(F)`, `ERE\ovf-telemetry:(RX)`, `BUILTIN\Administrators:(F)` |
| `C:\ProgramData\OJU Video Forge\telemetry\audit.log` | SYSTEM | `SYSTEM:(F)`, `ERE\ovf-telemetry:(RW)`, `BUILTIN\Administrators:(R)` |

---

## 10. Local audit logs

- **Path:** `C:\ProgramData\OJU Video Forge\telemetry\audit.log`
- **Content:** Each run records: timestamp, exit code, RAM bytes, GPU bytes, sequence number, HMAC key ID, errors
- **Rotation:** Max 10 MB per file, max 5 rotated files
- **ACL:** `SYSTEM:(F)`, `ERE\ovf-telemetry:(RW)`, `BUILTIN\Administrators:(R)`
- **Event log:** Task Scheduler also records task start/completion/failure in the Windows Event Log (`Microsoft-Windows-TaskScheduler/Operational`)

---

## 11. Key rotation

- **Rotation interval:** 90 days
- **Procedure:**
  1. Generate new 32-byte key
  2. Write to `hmac.key.new` with same ACLs in the secrets directory
  3. Update both publisher and consumer configurations to reference new key path
  4. Atomic rename `hmac.key.new` → `hmac.key`
  5. Retire old key after all consumers have rotated (minimum 24 hours)
- **Old key retention:** Kept for 30 days for verification of in-flight observations

---

## 12. Uninstall and rollback

**Uninstall:**
1. Delete the scheduled task (`OJU Host Telemetry Publisher`)
2. Remove `ERE\ovf-telemetry` account
3. Delete `C:\ProgramData\OJU Video Forge\telemetry\` directory (after backing up state.json)
4. Delete `C:\ProgramData\OJU Video Forge\secrets\` directory (after key rotation backup)
5. Remove `SeServiceLogonRight` from account (if account retained)

**Rollback:**
1. Disable (not delete) the scheduled task
2. Restore previous `observation.json` and `state.json` from backup
3. Restore previous HMAC key if rotated
4. Re-enable task when ready

**Re-enablement:** Requires explicit owner approval; task remains disabled by default after install

---

## 13. Secret verification

- **No secrets in command-line arguments:** The task action runs the publisher script with no key arguments
- **No secrets in task definition:** `INFERENCE_TELEMETRY_HMAC_KEY_FILE` is set to a file path, not the key value
- **No secrets in environment variables visible to consumers:** Key file is mounted as a read-only secret file; key value is read from disk at runtime
- **Verification:** `schtasks /query /tn "OJU Host Telemetry Publisher" /xml` confirms no secret values in task XML
- **Mount audit:** Consumers mount only `/run/secrets/telemetry-hmac.key:ro` (single file); the secrets directory itself is never mounted, so consumers cannot browse the publisher's key-storage location

---

## 14. Explicitly NOT included (by design)

- **No persistent Windows service** — uses Task Scheduler, not a service (per constraint: no persistent Windows telemetry service without owner authorization)
- **No remote telemetry transmission** — local file output only; consumers (API/worker) read the file via bind-mounted volume
- **No GPU process attribution** — aggregate GPU capacity only via nvidia-smi; WDDM process attribution explicitly out of scope
- **No automatic activation** — task is created in disabled state; owner must explicitly enable
- **No task registration by non-administrator** — current interactive user (`akoso`) is not a member of Administrators; task registration requires an authorized administrator or deployment identity with `SeServiceLogonRight` assignment rights

---

## 15. Approval checklist

Before owner approval, confirm:

- [ ] `ERE\ovf-telemetry` account created with correct privileges
- [ ] HMAC key generated and placed in secrets directory with correct ACLs
- [ ] Key file is outside the observation directory (no key in telemetry/ mount)
- [ ] Task definition XML reviewed for absence of secrets
- [ ] Task in **disabled** state after install
- [ ] `collect-host-telemetry.ps1` verified on ERE host (exit 0, correct output)
- [ ] `publish-host-telemetry.py` verified on ERE host (single-shot exit 0, daemon overlap prevention works)
- [ ] `INFERENCE_TELEMETRY_MAX_AGE_SECONDS=30` configured in candidate services
- [ ] `INFERENCE_TELEMETRY_CLOCK_SKEW_SECONDS=5` configured in candidate services
- [ ] `INFERENCE_TELEMETRY_HMAC_KEY_FILE` configured in candidate services (key file path, not key value)
- [ ] Rollback procedure tested

**Owner approval required for:** task creation, task enablement, key deployment, scheduled-task activation

**Administrator action required:** Task registration cannot be performed by the current interactive user (`akoso` is not a member of `BUILTIN\Administrators`). An authorized administrator or deployment identity must:
1. Create the `ERE\ovf-telemetry` account (or confirm it exists)
2. Assign `SeServiceLogonRight` to `ERE\ovf-telemetry`
3. Register the `OJU Host Telemetry Publisher` scheduled task
4. Enable the task when ready for production

---
