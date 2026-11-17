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

### Role separation and routing
- **Vision role:** uses approved `AGENT_*` / `NARRATION_VISION_*` configuration and the
  approved multimodal model (`qwen3-vl:4b`, pinned by full digest) over the canonical
  internal OJU Ollama endpoint `http://ollama:11434` on `agent-inference` (no
  published ports). Host-local endpoints are allowed only as explicit development/test
  overrides where already supported. `app.narration.vision()` preserves Qwen3-VL image
  payloads, model/digest identity checks, and vision provenance fields while executing
  through the shared coordinated Ollama transport/adapter path.
- **Training-text role:** uses `TRAINING_TEXT_*` exclusively
  (`TRAINING_TEXT_PROVIDER`, `TRAINING_TEXT_URL`, `TRAINING_TEXT_MODEL`,
  `TRAINING_TEXT_MODEL_DIGEST`, and stage/generation timeouts). Canonical endpoint is
  `http://ollama:11434`. There is no fallback to `AGENT_MODEL`, `NARRATION_VISION_MODEL`,
  an arbitrary installed Ollama model, or a host Ollama endpoint. Missing/unavailable
  training-text configuration fails closed for writing tasks only; startup and unrelated
  health remain unaffected.
- Shared bounded Ollama transport behavior (timeouts, identity checks, structured output
  handling) is used while role selection, provenance, and readiness remain distinct.
- `TRAINING_TEXT_*` is propagated only to services that require inference (API and
  render-worker), not to PostgreSQL, Redis, Docling OCR, ComfyUI, migrate, or unrelated
  services.

### Shared Ollama HTTP request guards
- All application-originated Ollama HTTP calls in `LocalOllamaAdapter._request` require
  an active inference coordination context and verify current Redis lease ownership before
  initiating the model request (`require_active_inference_lease` -> `ensure_lease_owned`).
- Requests fail closed when there is no active lease context, Redis ownership cannot be
  confirmed, ownership is lost, telemetry/admission checks fail, or coordination is
  unavailable. Direct vision calls do not bypass coordination; narration vision uses the
  guarded adapter request path.
- No coordination/lease guards are added around CPU OCR or FFmpeg processing.

### Admission, lease, and telemetry guarantees
- A shared Redis lease (`oju-video-forge:inference:lease`) serializes OJU heavy inference
  admission at enforced boundaries (narration worker execution and API agent proposal),
  with unique owner, bounded wait, renewal, owner-guarded release, cancellation checks,
  and ownership checks that fail closed on loss.
- Admission preflight is fail-closed on Redis unavailability, lease timeout, missing/stale/
  invalid/unverified telemetry, insufficient host available RAM (< 4 GiB reserve), or
  insufficient GPU capacity as reported by trusted telemetry. GPU free-VRAM admission can
  require a measured minimum when provided.
- Telemetry contract (source): preferred signed `INFERENCE_TELEMETRY_JSON` envelope with
  HMAC-SHA256 integrity validation (`INFERENCE_TELEMETRY_HMAC_KEY`) over canonical payload
  fields including collected timestamp, max age, source identity, available RAM, GPU
  capacity/free/total fields, and measurement status as applicable; constant-time signature
  comparison is used. Invalid/missing HMAC, tampered payloads, expired measurements,
  future-dated measurements beyond allowed skew, malformed fields, and unsigned strict-mode
  inputs are rejected. Unsigned env-injected measurements are a test/development mechanism
  only when explicitly allowed and fresh; static values are not proof of current capacity.
- **Replay:** without a nonce/one-time token cache, a valid signed measurement may be
  replayable within its freshness window; replay prevention is not implemented.

### Vision acceptance contract (separate from instructional-writing Stage A)
- Minimal vision observation/provenance contract (mock-validated in source tests): source
  frame/evidence context supplied by caller/workflow, observed interface elements/text in
  bounded observation text, uncertainty qualifier, model identity and full digest, and
  provenance fields (including model-info hash where applicable). Observed user actions may
  be included only when directly supported by evidence; unsupported claims must not become
  approved procedural evidence. Human review/approval gates remain required.
- Real Qwen3-VL structured vision acceptance is not claimed without bounded real-model tests.

### Readiness and provenance
- Readiness/capabilities distinguish vision configured/available, OCR available, and
  training-text configured/available/runtime-validated. Training-text is not reported ready
  from configuration-only state or `/api/tags` metadata alone.
- Training provenance records role `training_text`, provider/endpoint identity, model name
  and full digest, runtime version, generation stage, schema version, approved evidence
  revision, and schema/evidence validation results, while retaining separate vision/OCR
  provenance. Frame IDs, timestamps, approvals, and actions are never invented.

## Unresolved runtime limitations
- **No strict end-to-end exclusivity claim:** admission/boundary serialization is enforced at
  wired OJU code paths, but a single external inference gateway/proxy is not implemented.
  Lease expiry/renewal loss cannot interrupt an in-flight Ollama request and cannot prove
  remote termination; expired/lost lease must not authorize conflicting model transitions,
  but in-flight operations may continue until runtime/network boundaries complete or fail.
- **Crash recovery:** Redis TTL expiry allows bounded recovery after worker/process crash;
  no unsafe automatic lease takeover is performed while ownership/liveness cannot be proven.
- **Telemetry publisher:** a Windows host-side publisher with fresh authenticated/signed
  delivery for available physical RAM and NVIDIA free/total VRAM is not implemented or
  deployed; static/test env injection is not a production telemetry source.
- **Model/runtime acceptance:** real dual-model runtime acceptance, vision Stage A structured
  acceptance on live model, and deployment/runtime validation are not established by source
  tests.

## Consequences

- Operators must configure/pin approved models and provide trusted, fresh telemetry before
  heavy inference admission can succeed in strict mode.
- Vision and writing can be validated and diagnosed independently.
- Coordination behavior is source-level and boundary-level; runtime guarantees depend on
  complete caller coverage, Redis/telemetry health, and Ollama/runtime behavior.

## Validation, rollback, and deployment boundaries
- Source validation includes unit/integration tests (mocked inference/Redis/telemetry where
  applicable), Ruff checks for changed code, repository validation, and compose config
  validation. Mocked tests are not live Ollama or deployment acceptance.
- Rollback is source-level (revert commit/changes) until deployed; no containers/services are
  restarted, deployed, pushed, or merged by this ADR change.
- OCR, Whisper/ASR, and Docling paths remain outside Ollama model-transition coordination
  except existing service/resource constraints.

