# OJU Video Forge — Phase 14 Implementation and Acceptance Report

**Branch:** `feat/silent-recording-training`
**Starting SHA:** `cda0959`
**Resulting SHA:** `6a8132f`
**Date:** 2026-11-19

---

## 1. Git Worktree Disposition

| File | Classification | Action | Commit SHA |
|---|---|---|---|
| `scripts/provision_dev_auth.py` | Reusable tooling | Sanitized + committed | `17a1d02` |
| `scripts/register-telemetry-task.ps1` | Reusable tooling | Sanitized + committed | `17a1d02` |
| `scripts/_fix_gate2.py` | Superseded experimental | Discarded | N/A |
| `scripts/verify_dev_stack.py` | Reusable tooling | Created + committed | `e83c200` |
| `scripts/verify_dev_stack.py` | Fix unused variable | Fixed + committed | `6a8132f` |
| `.env.example` | Configuration template | Fixed HMAC key reference | `540e47e` |
| `.vscode/settings.json` | Local IDE setting | Preserved (untracked) | N/A |
| `.phase12-*` | Phase 12 diagnostics | Preserved (untracked) | N/A |
| `artifacts-dev/` | Phase 12 artifacts | Preserved (untracked) | N/A |
| `scan_artifacts/` | Diagnostic outputs | Preserved (untracked) | N/A |
| `scripts/gate2_publish.py` | Gate 2 publication bridge | Preserved (untracked) | N/A |
| `.env` | Secret material | Preserved (untracked, gitignored) | N/A |

**No tracked modifications remain. No secrets committed.**

---

## 2. Files Added/Modified/Committed

| Commit | Files | Description |
|---|---|---|
| `17a1d02` | `scripts/provision_dev_auth.py`, `scripts/register-telemetry-task.ps1` | Sanitized operational scripts |
| `e83c200` | `scripts/verify_dev_stack.py` | Dev stack bootstrap verification |
| `540e47e` | `.env.example` | HMAC key file reference fix |
| `6a8132f` | `scripts/verify_dev_stack.py` | Unused variable fix |

**New documentation files (untracked):**
- `docs/ere-development-baseline-register.md`
- `docs/image-provenance-register.md`
- `docs/dev-secrets-rotation-runbook.md`
- `docs/telemetry-health-recovery-runbook.md`
- `docs/backup-restore-portability-runbook.md`
- `docs/adr-ovf-003-gateway-proposal.md`
- `docs/narration-rendering-quality-report.md`

---

## 3. Actual Running Image Digests

| Service | Image | Digest |
|---|---|---|
| API | `oju-video-forge-api:latest` | `sha256:c343e97af87c12e2759d3ca25e1bc22c49412c665116a4521ff64d34c6fca9e7` |
| Render-worker | `oju-video-forge-render-worker:latest` | `sha256:3210a5cdddb9eb6f5a3d6abfde42fb204d2ede84bf978ea45ae077bc81d3c333` |
| Ollama | `ollama/ollama:0.40.16` | `sha256:a372a1c0d1e15b68e9521189785c3ca17795c26c2356f8b1b10122cfb59ed0d7` |
| PostgreSQL | `postgres:17.7-alpine3.22` | `sha256:6b591f995765a189e69276dd55e0b362342d65d10d0359bb1fab67bc3391f20f` |
| Redis | `redis:7.4-alpine` | `sha256:c5d34a98396a4775e223c74e929162c35c5a2182901ba26b50ade939941ce14b` |

---

## 4. Reproducible Development Bootstrap Status

**PASS** — `scripts/verify_dev_stack.py` validates:

- Compose overlay merges (base, integration, ollama): PASS
- Network membership: PASS
- Image versions: PASS
- Ollama model endpoints/digests: PASS
- Secret mounts: PASS
- Telemetry publisher freshness: PASS
- TTS/FFmpeg availability: PASS
- Non-root execution/read-only rootfs: PASS

The stack can be rebuilt from documented configuration without undocumented manual fixes.

---

## 5. Authentication and Secret Protection Results

| Check | Result |
|---|---|
| No placeholder bearer tokens | PASS (all tokens provisioned) |
| Credentials random | PASS (secrets.token_urlsafe(32)) |
| Tokens not committed to Git | PASS (.env gitignored) |
| Token values not in logs | PASS (no token logging) |
| Role distinction (user/reviewer/admin) | PASS |
| Project membership enforced | PASS |
| `.env` excluded from version control | PASS |
| HMAC key via file mount, not env var | PASS |
| Key stored outside observation mount | PASS |

---

## 6. Telemetry Reliability Measurements

| Metric | Value |
|---|---|
| Publication interval | 10 seconds |
| Observation freshness | PASS (2.1s age, 30s max) |
| Source identity | ere-host |
| HMAC integrity | PASS (verified) |
| Sequence monotonicity | PASS (seq 7948, advancing) |
| Publisher process count | 1 (Windows Scheduled Task) |
| Task state | Running |
| Restart behavior | RestartCount 3, 1-minute interval |
| Replay-state persistence | PASS (writable volume) |
| Available RAM | ~16 GB |
| Available GPU VRAM | 15.5 GB free of 17.1 GB |
| Redis availability | PASS (22 keys) |
| Ollama reachability | PASS (private network) |
| API/worker readiness | PASS |

---

## 7. Inference Safety Review

| Property | Status |
|---|---|
| Redis lease atomic ownership | PASS (nx=True) |
| Lease renewal checks ownership | PASS (Lua script) |
| Lease release checks ownership | PASS (Lua script) |
| Model request validates lease ownership | PASS (_assert_owned) |
| Telemetry admission mandatory | PASS (preflight_heavy_inference) |
| Stale telemetry blocks transitions | PASS (freshness + integrity) |
| Cancellation propagated | PASS (check() callback) |
| Nested lease deadlocks eliminated | PASS (Phase 13 fix) |
| Timeout handling bounded | PASS (wait_seconds deadline) |
| Model identity/digest enforcement | PASS (adapter config) |
| **Remaining gap** | Lease cannot interrupt in-flight Ollama request |

**ADR-OVF-003 disposition:** Proposed. Execution gateway BLOCKED pending authorization. Gateway proposal documented in `docs/adr-ovf-003-gateway-proposal.md`.

---

## 8. Narration/Rendering Quality Results

| Attribute | Result |
|---|---|
| TTS provider | espeak-ng 1.52.0 (GPL-3.0-or-later) |
| Voice | en-us, 160 wpm |
| Audio codec | PCM s16le, 22050 Hz, mono |
| Audio duration | 49.98s |
| Audio non-silent | PASS (verified via FFprobe) |
| Video codec | MPEG-4 Part 2 |
| Video resolution | 640×360 |
| Video frame rate | 10 fps |
| Video duration | 50.000s |
| AAC audio in video | PASS |
| Output SHA256 | bc02763411a2e200c60b64a132bccb281563cbb48d13f9f2bab05269b7043da2 |
| H.264 encoder available | PASS (libx264) |
| H.264 test encode | PASS |
| H.264 production default | NOT TESTED (not changed) |

---

## 9. Full Regression Test Counts

| Suite | Passed | Skipped | Failed |
|---|---|---|---|
| Full test suite | 456 | 16 | 0 |
| Ruff (changed files) | Clean | N/A | N/A |

Skips are expected: job-dispatch integration tests (require test databases), bash-dependent validation scripts, optional ASR model.

---

## 10. Remaining Operational Risks

| Risk | Classification | Mitigation |
|---|---|---|
| Strict inference exclusivity | BLOCKED | ADR-OVF-003 Proposed; gateway proposal documented |
| H.264 not production default | NOT TESTED | libx264 available; document as backlog item |
| Backup/rehearsal not executed | NOT TESTED | Procedure documented; isolated rehearsal recommended |
| Windows Task Scheduler dependency | PARTIAL | Task registered and running; Linux alternative needed for migration |
| Dev bearer token expiry | PARTIAL | 30-day expiry; rotation procedure documented |

---

## 11. Prioritized Next Product-Development Backlog

1. **HIGH** — Execute ADR-OVF-003 gateway authorization decision (separate architectural review)
2. **HIGH** — Perform backup/restore rehearsal using isolated resources
3. **MEDIUM** — Evaluate H.264 as optional output profile (run regression tests first)
4. **MEDIUM** — Evaluate alternative offline TTS providers for improved intelligibility
5. **LOW** — Add silence trimming, loudness normalization, audio fades
6. **LOW** — Document Linux migration path (replace Windows Task Scheduler with systemd/cron)

---

## 12. ERE Development Baseline Readiness Classification

| Area | Classification |
|---|---|
| Training generation | PASS |
| Human script approval | PASS |
| Local TTS | PASS |
| Video rendering | PASS |
| Telemetry integrity | PASS |
| Evidence provenance | PASS |
| Authentication/authorization | PASS |
| Network isolation | PASS |
| Non-root security | PASS |
| Inference safety | PASS (with documented gap) |
| Reproducible bootstrap | PASS |
| Secret protection | PASS |
| ADR-OVF-003 gateway | BLOCKED |
| H.264 output | NOT TESTED |
| Backup/rehearsal | NOT TESTED |

**Overall: PASS** — OJU Video Forge has a reproducible and secured ERE development baseline that preserves its successful Phase 13 workflow. The system can be rebuilt from documented configuration. The remaining inference-exclusivity limitation is explicitly documented rather than hidden.
