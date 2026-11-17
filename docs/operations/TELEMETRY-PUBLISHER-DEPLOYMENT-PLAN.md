# OJU Video Forge — Host Telemetry Publisher: Scheduled-Task Deployment Plan (Gate 4)

**Target host:** ERE (Windows 11, `CSName=ERE`)
**Component:** `scripts/collect-host-telemetry.ps1` + `app/host_telemetry.py`
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

## 2. Protected HMAC signing key

- **Key file:** `C:\ProgramData\OJU Video Forge\telemetry\hmac.key` (32 random bytes)
- **ACL:** `SYSTEM:(F)`, `ERE\ovf-telemetry:(R)`, `BUILTIN\Administrators:(F)`; inheritance disabled
- **Encryption:** EFS-encrypted or DPAPI-protected at rest (machine-level)
- **Delivery:** Out-of-band via secure channel; never in task definition, never in command-line arguments, never in environment variables
- **Task definition:** References the key only via `INFERENCE_TELEMETRY_HMAC_KEY` environment variable set to the **file path**, not the key value. The collector script reads the file at runtime.
- **Verification:** `read_trusted_telemetry()` in `app/inference_coordination.py` verifies HMAC using the key supplied via `INFERENCE_TELEMETRY_HMAC_KEY` environment variable

---

## 3. Collection interval

- **Interval:** 15 seconds
- **Freshness limit:** `INFERENCE_TELEMETRY_MAX_AGE_SECONDS=30` (configured in candidate services)
- **Justification:** 15s < 30s freshness limit, providing at least two observations within the freshness window; accommodates clock skew of up to 5s (`INFERENCE_TELEMETRY_CLOCK_SKEW_SECONDS=5`)
- **Schedule trigger:** Daily trigger with 15-second repetition, indefinitely

---

## 4. Retry and failure policy

- **Retry count:** 3 attempts
- **Retry interval:** 30 seconds
- **Failure action:** Continue to next scheduled run; do not restart the task
- **Error handling:** Collector exits non-zero on failure; Task Scheduler records the event; no silent fallback
- **No backoff storm:** Fixed 30s retry interval, not exponential

---

## 5. Task startup behavior

- **Start trigger:** At system boot (delay 60s to allow network and time sync)
- **Repeat:** Every 15 seconds for the duration of the session
- **Stop if running longer than:** 5 minutes (prevents hung collector from blocking subsequent runs)
- **Start only if:** Network is available (to allow future remote endpoints, though current design is local-only)
- **Wake to run:** Disabled (no wake timers)
- **Hidden:** True (no UI)

---

## 6. Minimum privileges

- **Task rights:** `SeServiceLogonRight` for `ERE\ovf-telemetry` only
- **File system:**
  - Collector script (`scripts/collect-host-telemetry.ps1`): `ERE\ovf-telemetry:(RX)`, `SYSTEM:(F)`, `BUILTIN\Administrators:(F)`
  - Output directory (`C:\ProgramData\OJU Video Forge\telemetry\`): `ERE\ovf-telemetry:(RW)` (for state.json and observation.json output), `SYSTEM:(F)`, `BUILTIN\Administrators:(F)`
  - HMAC key: `ERE\ovf-telemetry:(R)` only
- **Registry:** No write access required
- **Network:** No outbound network required (local NMI/WMI/nvidia-smi calls only)
- **Elevation:** Does not require `SeDebugPrivilege` or `SeTcbPrivilege`

---

## 7. Atomic publication

The collector writes telemetry atomically:

1. Collect RAM (GlobalMemoryStatusEx) and GPU capacity (nvidia-smi) into a temporary structure
2. Serialize to canonical JSON (sorted keys, fixed separators)
3. Compute HMAC-SHA256 over the canonical bytes using the signing key
4. Write envelope `{payload, signature, key_id, source_id, timestamp, sequence}` to a temporary file in the same directory
5. `MoveFileEx` with `MOVEFILE_REPLACE_EXISTING` (atomic rename on NTFS)
6. Update sequence file (also atomic via rename)

The `apply_file_protection` function in `app/host_telemetry.py` applies ACLs (`ERE\akoso:(R)`, `BUILTIN\Administrators:(F)`) to the output file after publication.

---

## 8. Protected ACLs

| Path | Owner | ACL |
|------|-------|-----|
| `C:\ProgramData\OJU Video Forge\telemetry\hmac.key` | SYSTEM | `SYSTEM:(F)`, `ERE\ovf-telemetry:(R)` |
| `C:\ProgramData\OJU Video Forge\telemetry\observation.json` | SYSTEM | `SYSTEM:(F)`, `ERE\ovf-telemetry:(RW)`, `BUILTIN\Administrators:(F)` |
| `C:\ProgramData\OJU Video Forge\telemetry\state.json` | SYSTEM | `SYSTEM:(F)`, `ERE\ovf-telemetry:(RW)` |
| `C:\ProgramData\OJU Video Forge\telemetry\*.lock` | SYSTEM | `SYSTEM:(F)`, `ERE\ovf-telemetry:(RW)` |
| `scripts/collect-host-telemetry.ps1` | SYSTEM | `SYSTEM:(F)`, `ERE\ovf-telemetry:(RX)`, `BUILTIN\Administrators:(F)` |
| `C:\ProgramData\OJU Video Forge\telemetry\audit.log` | SYSTEM | `SYSTEM:(F)`, `ERE\ovf-telemetry:(RW)`, `BUILTIN\Administrators:(R)` |

---

## 9. Local audit logs

- **Path:** `C:\ProgramData\OJU Video Forge\telemetry\audit.log`
- **Content:** Each run records: timestamp, exit code, RAM bytes, GPU bytes, sequence number, HMAC key ID, errors
- **Rotation:** Max 10 MB per file, max 5 rotated files
- **ACL:** `SYSTEM:(F)`, `ERE\ovf-telemetry:(RW)`, `BUILTIN\Administrators:(R)`
- **Event log:** Task Scheduler also records task start/completion/failure in the Windows Event Log (`Microsoft-Windows-TaskScheduler/Operational`)

---

## 10. Key rotation

- **Rotation interval:** 90 days
- **Procedure:**
  1. Generate new 32-byte key
  2. Write to `hmac.key.new` with same ACLs
  3. Update both collector and consumer configurations to reference new key path
  4. Atomic rename `hmac.key.new` → `hmac.key`
  5. Retire old key after all consumers have rotated (minimum 24 hours)
- **Key ID:** Included in telemetry envelope so consumers can identify which key signed each observation
- **Old key retention:** Kept for 30 days for verification of in-flight observations

---

## 11. Uninstall and rollback

**Uninstall:**
1. Delete the scheduled task (`OJU Host Telemetry Publisher`)
2. Remove `ERE\ovf-telemetry` account
3. Delete `C:\ProgramData\OJU Video Forge\telemetry\` directory (after backing up state.json)
4. Remove `SeServiceLogonRight` from account (if account retained)

**Rollback:**
1. Disable (not delete) the scheduled task
2. Restore previous `observation.json` and `state.json` from backup
3. Restore previous HMAC key if rotated
4. Re-enable task when ready

**Re-enablement:** Requires explicit owner approval; task remains disabled by default after install

---

## 12. Secret verification

- **No secrets in command-line arguments:** The task action runs `powershell.exe -ExecutionPolicy Bypass -File <script-path>` with no key arguments
- **No secrets in task definition:** `INFERENCE_TELEMETRY_HMAC_KEY` is set to a file path, not the key value
- **No secrets in environment variables:** Key value is read from disk at runtime by the collector script
- **Verification:** `schtasks /query /tn "OJU Host Telemetry Publisher" /xml` confirms no secret values in task XML

---

## 13. Explicitly NOT included (by design)

- **No persistent Windows service** — uses Task Scheduler, not a service (per constraint: no persistent Windows telemetry service without owner authorization)
- **No remote telemetry transmission** — local file output only; consumers (API/worker) read the file via bind-mounted volume
- **No GPU process attribution** — aggregate GPU capacity only via nvidia-smi; WDDM process attribution explicitly out of scope
- **No automatic activation** — task is created in disabled state; owner must explicitly enable

---

## 14. Approval checklist

Before owner approval, confirm:

- [ ] `ERE\ovf-telemetry` account created with correct privileges
- [ ] HMAC key generated and placed with correct ACLs
- [ ] Task definition XML reviewed for absence of secrets
- [ ] Task in **disabled** state after install
- [ ] `collect-host-telemetry.ps1` verified on ERE host (exit 0, correct output)
- [ ] `INFERENCE_TELEMETRY_MAX_AGE_SECONDS=30` configured in candidate services
- [ ] `INFERENCE_TELEMETRY_CLOCK_SKEW_SECONDS=5` configured in candidate services
- [ ] Rollback procedure tested

**Owner approval required for:** task creation, task enablement, key deployment, scheduled-task activation
