# OJU Video Forge — ERE Development Baseline Register

**Branch:** `feat/silent-recording-training`
**Register date:** 2026-11-19
**Current commit:** `6a8132f`

---

## Baseline Summary

The ERE development baseline preserves all Phase 13 capabilities:

- Persisted TrainingDraft generation via `qwen2.5:7b-instruct` (digest `845dbda0...`)
- Authenticated human script approval via bearer tokens
- Offline speech synthesis via `espeak-ng` 1.52.0
- Local video rendering with FFmpeg 6.1.2 (MPEG-4 Part 2 + AAC)
- Full evidence-to-video provenance
- Telemetry integrity via HMAC-SHA256
- Non-root container execution with read-only rootfs
- Redis inference coordination with atomic leases

---

## Component Register

| Component | Version/Digest | Status |
|---|---|---|
| API image | `oju-video-forge-api:latest` `sha256:c343e97a...` | Running |
| Render-worker image | `oju-video-forge-render-worker:latest` `sha256:3210a5cd...` | Running |
| Ollama | `ollama/ollama:0.40.16@sha256:a372a1c0...` | Running |
| PostgreSQL | `postgres:17.7-alpine3.22@sha256:6b591f99...` | Running |
| Redis | `redis:7.4-alpine@sha256:c5d34a98...` | Running |
| Writing model | `qwen2.5:7b-instruct` `845dbda0ea48...` | Provisioned |
| Vision model | `qwen3-vl:4b` `a1b29c246319...` | Provisioned |
| TTS provider | `espeak-ng` 1.52.0 (GPL-3.0-or-later) | Installed |
| FFmpeg | 6.1.2 | Installed |

---

## Verified Capabilities

| Capability | Evidence |
|---|---|
| Training generation | 456 tests pass, TrainingDraft persisted |
| Human script approval | API endpoints tested, authz enforced |
| Local TTS | espeak-ng 1.52.0 in both containers |
| Video rendering | video.mp4 330KB, AAC audio 49.98s |
| Telemetry integrity | HMAC verified, fresh observations |
| Non-root execution | UID 10001, read-only rootfs |
| Network isolation | agent-inference private network |

---

## Known Limitations

| Limitation | Classification | Mitigation |
|---|---|---|
| Strict inference exclusivity | BLOCKED | ADR-OVF-003 Proposed; gateway not implemented |
| H.264 output | NOT TESTED | libx264 available but not production default |
| Windows task scheduler | PARTIAL | Task registered, running under ERE\akoso |
| Backup/rehearsal | NOT TESTED | Procedure documented, not executed |

---

## Rollback References

Previous image identities preserved (see docs/image-provenance-register.md).
