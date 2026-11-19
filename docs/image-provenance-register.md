# OJU Video Forge — Image Provenance and Rollback Register

**Branch:** `feat/silent-recording-training`
**Register date:** 2026-11-19
**Source commit:** `e83c200` (HEAD at register time)

---

## Running Production-Like Images

| Image | Tag | Immutable Digest | Build Source SHA | Created |
|---|---|---|---|---|
| `oju-video-forge-api` | latest | `sha256:c343e97af87c12e2759d3ca25e1bc22c49412c665116a4521ff64d34c6fca9e7` | `cda0959` | 2026-11-19T04:04:09Z |
| `oju-video-forge-render-worker` | latest | `sha256:3210a5cdddb9eb6f5a3d6abfde42fb204d2ede84bf978ea45ae077bc81d3c333` | `cda0959` | 2026-11-19T04:04:06Z |

---

## Base / Dependency Images

| Image | Digest | Created | Role |
|---|---|---|---|
| `python:3.12-alpine3.22` | `sha256:a190708a2dec1bd18b1decb539f8e8f5407abaa9bf39cacda583f7f8c11db322` | 2026-11-09 | API/worker base |
| `ollama/ollama` | `sha256:a372a1c0d1e15b68e9521189785c3ca17795c26c2356f8b1b10122cfb59ed0d7` | 2026-11-09 | Inference endpoint |
| `postgres:17.7-alpine3.22` | `sha256:6b591f995765a189e69276dd55e0b362342d65d10d0359bb1fab67bc3391f20f` | 2026-01-28 | Application database |
| `redis:7.4-alpine` | `sha256:c5d34a98396a4775e223c74e929162c35c5a2182901ba26b50ade939941ce14b` | 2026-11-05 | Inference coordination / Redis lease |

---

## Build Source Provenance

### API image (`cda0959`)

**Build context:** Repository root, `services/api/Dockerfile`, target `runtime`

**Commit `cda0959`** — "Gate 3-4: Add espeak-ng TTS runtime to API and worker images"
- `services/api/Dockerfile`: added `espeak-ng=1.52.0-r0`
- `services/worker/Dockerfile`: added `espeak-ng=1.52.0-r0`

**Parent commit `490fce1`** — Phase 13 infrastructure
- `compose.yaml`: agent-inference network
- `compose.integration.yaml`: frontend port override
- `compose.ollama.yaml`: network isolation
- `services/docker-entrypoint.sh`: non-fatal telemetry-state chown
- `tests/test_compose_isolation.py`: regression tests

**Parent commit `413950b`** — Gate 4 fixes
- `app/narration_jobs.py`: removed nested inference lease deadlock; fixed stage provenance sequence

**Dependency lockfile:** `requirements.lock.txt` (hash-pinned Python dependencies)

### Render-worker image (`cda0959`)

**Build context:** Repository root, `services/worker/Dockerfile`

Same commit provenance as API image.

---

## Container Capabilities and Security

| Service | User | Read-only rootfs | Cap drop | Cap add | Security opt |
|---|---|---|---|---|---|
| api | forge (10001) | true | ALL | SETUID, SETGID | no-new-privileges |
| render-worker | forge (10001) | true | ALL | SETUID, SETGID | no-new-privileges |
| ollama | root | true | ALL | none | no-new-privileges |
| postgres | postgres | false | none | none | no-new-privileges |
| redis | redis | false | none | none | no-new-privileges |

---

## Functional Test Evidence

| Test suite | Result |
|---|---|
| `tests/test_resilience.py` | 14 passed |
| `tests/test_validation_scripts.py` | 9 passed |
| `tests/test_compose_isolation.py` | 6 passed |
| `tests/test_training.py` | 62 passed (combined training suite) |
| `tests/test_training_step_coverage.py` | included above |
| `tests/test_agents.py` | included above |
| `tests/test_adapters.py` | included above |
| **Full suite** | **456 passed, 16 skipped** |
| Ruff (changed files) | Clean |

Skips are expected: job-dispatch integration tests (require test databases), bash-dependent validation scripts (bash unavailable in container), optional ASR model (not provisioned).

---

## Rollback References

| Image | Tag | Digest | Status |
|---|---|---|---|
| `oju-video-forge-api` | rollback-pre-training-67460a9 | `sha256:12fb8d832ef8` | Preserved |
| `oju-video-forge-api` | rollback-67460a9 | `sha256:2c9dd87b2a52` | Preserved |
| `oju-video-forge-api` | gate6-fix | `sha256:abbac498c68b` | Preserved |
| `oju-video-forge-render-worker` | rollback-pre-training-67460a9 | `sha256:f1a2514b551b` | Preserved |
| `oju-video-forge-render-worker` | rollback-67460a9 | `sha256:51ce99d59bc` | Preserved |
| `oju-video-forge-render-worker` | rollback-pre-vision512-67460a9 | `sha256:be01cbcc4d6b` | Preserved |

---

## SBOM and Scanner Status

SBOMs and vulnerability scans are generated in CI (Trivy). Scanner exceptions are documented in `.github/` workflow configuration. The CI Trivy gate correctly retains unfixed findings per project constraint `ci_gate_retains_unfixed_findings`; `--ignore-unfixed` is not used.

**No new scanner exceptions were introduced in Phase 13/14.**

---

## Notes

- `latest` tags are mutable and should not be relied upon for production deployment. Immutable digests are recorded above.
- The `candidate-telemetry` tagged images (`sha256:66a33f07...`, `sha256:ca813cc4...`) were cutover targets for Phase 13 telemetry and remain available but are not the current running images.
- Rollback images are preserved and not subject to Docker cleanup.
