# ADR-OVF-003: Dual-Role AI Architecture (Vision and Training Text)

- Status: Proposed
- Date: 2026-11-16
- Owners: OJU Video Forge maintainers
- Related: [ADR-OVF-001](../../ADR-OVF-001.md), [ADR-OVF-002](../../ADR-OVF-002.md)

## Context

Video Forge uses local AI for two distinct responsibilities: (1) vision/OCR-assisted
evidence extraction for silent training and narration workflows, and (2) instructional
text generation for training drafts. These roles have different model, prompt, timeout,
and readiness characteristics. A single-role configuration couples them, makes
readiness ambiguous, and risks silent fallback to an unapproved model.

## Decision

- Introduce explicit dual-role inference configuration and adapters:
  - **Vision role:** uses approved `AGENT_*` / vision configuration and the approved
    multimodal model (`qwen3-vl:4b`, pinned by full digest) over the canonical
    internal OJU Ollama endpoint `http://ollama:11434` on `agent-inference` (no
    published ports). Host-local endpoints are allowed only as explicit
    development/test overrides where already supported. Existing vision frame
    analysis in `app/narration.py` remains env-driven and validates endpoint/model
    identity; scene analysis with `options.vision` is serialized through the shared
    narration inference boundary when available.
  - **Training-text role:** uses `TRAINING_TEXT_*` exclusively
    (`TRAINING_TEXT_PROVIDER`, `TRAINING_TEXT_URL`, `TRAINING_TEXT_MODEL`,
    `TRAINING_TEXT_MODEL_DIGEST`, and stage/generation timeouts). Canonical endpoint
    is `http://ollama:11434`. There is no fallback to `AGENT_MODEL`,
    `NARRATION_VISION_MODEL`, an arbitrary installed Ollama model, or a host Ollama
    endpoint. Missing or unavailable training-text configuration fails closed with an
    explicit blocked/unavailable state for writing tasks only; application startup and
    unrelated health remain unaffected.
- Share bounded Ollama transport behavior (timeouts, identity checks, structured
  output handling) while keeping role selection, provenance, and readiness distinct.
- Propagate `TRAINING_TEXT_*` only to services that require inference (API and
  render-worker), not to PostgreSQL, Redis, Docling OCR, ComfyUI, migrate, or
  unrelated services.
- Readiness/capabilities must distinguish: vision configured/available, OCR available,
  and training-text configured/available/runtime-validated. Training-text must not be
  reported ready from configuration-only state or `/api/tags` metadata alone.
- Training provenance records role `training_text`, provider/endpoint identity, model
  name and full digest, runtime version, generation stage, schema version, approved
  evidence revision, and schema/evidence validation results, while retaining separate
  vision/OCR provenance. Frame IDs, timestamps, approvals, and actions are never
  invented.
- Heavy inference/model transitions require cross-process coordination and fresh
  trusted resource admission:
  - A shared Redis lease (`oju-video-forge:inference:lease`) serializes OJU heavy
    inference admission at enforced boundaries (narration worker execution of
    `narrate_existing_video` phases and agent proposal generation in API).
  - Admission is fail-closed on Redis unavailability, lease timeout, stale/missing
    telemetry, insufficient host RAM (< 4 GiB available reserve), or insufficient
    GPU capacity as reported by trusted telemetry.
  - Telemetry must be fresh and explicit: signed `INFERENCE_TELEMETRY_JSON` with
    HMAC integrity validation is preferred; unsigned local env measurements are
    allowed only when `INFERENCE_TELEMETRY_ALLOW_UNSIGNED=true` and include a
    collection timestamp, max age, and source identity. Static values are not
    proof of current capacity.
  - Lease ownership is checked/released with owner-guarded Redis operations and
    ownership checks fail closed on loss. Lease expiry alone must not authorize a
    conflicting model transition after a remote Ollama request has started without
    runtime-level cancellation support; the implementation cannot interrupt an
    in-flight Ollama request.

## Consequences

- Operators must configure and pin an approved training-text model and provide
  trusted, fresh resource telemetry before heavy training-text/vision inference can
  be admitted.
- Vision and writing can be validated, rolled out, and diagnosed independently.
- Source-level serialization is enforced at implemented boundaries; end-to-end
  exclusivity depends on all OJU Ollama callers using the same boundary and on
  deployment/runtime telemetry integration.

## Validation Gates and Limitations

- Focused unit/integration tests cover role routing, fail-closed behavior, distinct
  readiness, provenance, and regression of existing agent/training behavior.
- Compose/config validation confirms canonical endpoint usage and scoped env
  propagation.
- Real model runtime acceptance remains blocked until an approved training-text
  model is configured/pinned and Stage A structured generation succeeds on the
  approved vision path.
- Running-image/deployment validation is not established by source tests alone.
- OCR, Whisper/ASR, and Docling paths are not Ollama model transitions but remain
  subject to existing service/resource constraints.
