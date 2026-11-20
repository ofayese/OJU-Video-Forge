# OJU Video Forge — ERE Development Baseline Register

**Branch:** `main`
**Register date:** 2026-11-20
**Current commit:** `80f49a7e241d0269dd5520e337cdfa64f49ca631`
**Remote:** `origin/main` at `80f49a7e241d0269dd5520e337cdfa64f49ca631` (synchronized)

---

## Baseline Summary

The ERE development baseline preserves all Phase 13 capabilities and adds Phase 14 qualification:

- Persisted TrainingDraft generation via `qwen2.5:7b-instruct` (digest `845dbda0...`)
- Authenticated human script approval via bearer tokens
- Offline speech synthesis via `espeak-ng` 1.52.0
- Local video rendering with FFmpeg (MPEG-4 Part 2 + AAC production default; H.264/AAC qualified via direct `media.mux()` execution)
- Full evidence-to-video provenance
- Telemetry integrity via HMAC-SHA256
- Non-root container execution with read-only rootfs
- Redis inference coordination with atomic leases
- Authenticated render submission through API → Redis dispatch → render-worker → FFmpeg
- Final-video human review with artifact checksum verification

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
| Rendered artifact | `bc02763411a2e200c60b64a132bccb281563cbb48d13f9f2bab05269b7043da2` (MPEG-4 Part 2/AAC, 640×360, 10fps, 50s, Phase 13) | Qualified |
| H.264 qualification output | `e374d461d6aeeba2916578c4dcceefd16770436436477c1b44216c53a946a409` (H.264 High/AAC, 640×360, 10fps, 50s) | Qualified |

---

## Verified Capabilities

| Capability | Evidence |
|---|---|
| Training generation | 456 tests pass, TrainingDraft persisted |
| Human script approval | API endpoints tested, authz enforced |
| Local TTS | espeak-ng 1.52.0 in both containers |
| Video rendering | MPEG-4 Part 2/AAC output via `media.mux()` copy mode, job `047f2e0f`, artifact SHA256 `bc027634...7043da2`, 330,516 bytes; H.264/AAC via direct `media.mux()` qualification, SHA256 `e374d461...a409`, 103,619 bytes | Qualified |
| Telemetry integrity | HMAC verified, fresh observations |
| Non-root execution | UID 10001, read-only rootfs |
| Network isolation | agent-inference private network |
| Authenticated render submission | API → Redis dispatch → render-worker → FFmpeg, end-to-end |
| Final-video human review | Artifact checksum verified, status `approved`, review_version 5 |
| Output quality | MPEG-4 Part 2, yuv420p, 640×360, 10fps, AAC 22050Hz mono, fast-start, A/V sync 0; H.264 qualification output verified separately |

---

## Known Limitations

| Limitation | Classification | Mitigation |
|---|---|---|
| Strict inference exclusivity | BLOCKED | ADR-OVF-003 Proposed; gateway not implemented |
| H.264 as production default | PARTIAL | Direct `media.mux()` H.264 qualified; production render endpoint uses `-c:v copy` (source codec passthrough) unless `render_annotations=True` |
| Windows task scheduler | PARTIAL | Task registered, running under ERE\akoso |
| Backup/rehearsal | NOT TESTED | Procedure documented, not executed |

---

## Requirement-to-Test Mapping

| Requirement | Classification | Test File(s) / Function(s) |
|---|---|---|
| Authenticated render submission | COVERED | `tests/test_narration.py::test_training_render_rechecks_procedure_approver_membership` |
| Project authorization | COVERED | `tests/test_narration.py::test_viewer_cannot_edit_approve_or_render_and_foreign_reader_is_denied` |
| Approved script revision enforcement | COVERED | `tests/test_narration.py::test_approval_is_revision_bound_and_edits_invalidate_it` |
| Redis job dispatch | COVERED | `tests/test_job_dispatch.py::test_submission_commits_job_and_outbox_before_dispatch_and_retries` |
| Render-worker processing | RUNTIME VERIFIED | `tests/test_render_worker.py::test_real_worker_render_records_provenance` |
| Media persistence | COVERED | `tests/test_narration.py::test_narration_job_artifact_uses_project_scoped_download` |
| H.264 codec-profile selection | RUNTIME VERIFIED | `tests/test_rendering.py::test_real_fixture_render_is_repeatable`, `tests/test_render_worker.py::test_real_worker_render_records_provenance` |
| Output codec verification | COVERED | `tests/test_rendering.py::test_real_fixture_render_is_repeatable` |
| Final-video human approval | COVERED | `tests/test_review.py::test_decision_and_immutable_history` |
| Stale artifact SHA rejection | COVERED | `tests/test_review.py::test_artifact_and_version_fail_closed` (checksum, id, actual, empty, missing) |
| Cross-project artifact access denial | COVERED | `tests/test_review.py::test_timeline_artifact_review_uses_project_role` |
| Invalid render parameters | COVERED | `tests/test_narration.py` (render with `render_annotations=True` and no reviewed items → 422) |
| Missing narration audio | PARTIAL | `tests/test_narration_media.py::test_mux_preserves_visuals_duration_and_deliberate_audio_mode` (audio mode coverage at media-library level; no API-level missing-audio failure test) |
| FFmpeg failure propagation | RUNTIME VERIFIED | `tests/test_render_worker.py::test_worker_failure_records_failure` |
| Cancellation | COVERED | `tests/test_job_dispatch.py::test_cancel_before_dispatch_closes_outbox` |
| Retry handling | COVERED | `tests/test_job_dispatch.py::test_submission_commits_job_and_outbox_before_dispatch_and_retries` |
| Evidence and model provenance | COVERED | `tests/test_narration.py::test_generation_stage_provenance_rejects_missing_or_mismatched_stages` |
| Review audit history | COVERED | `tests/test_review.py::test_decision_and_immutable_history` |

Acceptance levels (do not conflate):

1. FFmpeg encoder availability — COVERED (`tests/test_rendering.py::_h264_encoder`, `tests/test_narration_media.py::_h264_encoder`)
2. Direct media-library H.264/AAC encoding — RUNTIME VERIFIED (`tests/test_narration_media.py::test_mux_preserves_visuals_duration_and_deliberate_audio_mode`)
3. Authenticated API-to-worker rendering — RUNTIME VERIFIED (job `047f2e0f`, artifact SHA256 `bc027634...7043da2`)
4. Authorized final-video approval — COVERED (`tests/test_review.py::test_decision_and_immutable_history`)
5. Publication or external release — NOT TESTED (no external release pipeline implemented)

---

## Rollback References

Previous image identities preserved (see docs/image-provenance-register.md).

---

## Evidence Archive

Acceptance evidence is preserved outside the repository and outside `%TEMP%` at:

`C:\Users\akoso\Documents\OJU Evidence/`

This archive contains:

- `phase13/` — Phase 13 narrated video, narration audio, render provenance (3 files)
- `phase12/` — Phase 12 diagnostics, synthetic fixtures, request/response captures (41 files)
- `scans/` — Trivy SBOMs, compose effective config, runtime inspection artifacts (13 files)
- `qualified-render-output.mp4` — Phase 14 H.264 qualification artifact (SHA256 `e374d461d6aeeba2916578c4dcceefd16770436436477c1b44216c53a946a409`)
- `ojevidence-archive/` — Phase 14 runtime evidence: project-scoped artifact endpoint captures, review decision responses, auth negative test results
- `CHECKSUM-MANIFEST.md` — SHA256 checksum manifest for all archived artifacts

Phase 13 narrated video SHA256: `bc02763411a2e200c60b64a132bccb281563cbb48d13f9f2bab05269b7043da2` (MPEG-4 Part 2/AAC)
Phase 14 H.264 qualified output SHA256: `e374d461d6aeeba2916578c4dcceefd16770436436477c1b44216c53a946a409` (H.264 High/AAC)

No secrets, bearer tokens, or private keys are included in the archive.
