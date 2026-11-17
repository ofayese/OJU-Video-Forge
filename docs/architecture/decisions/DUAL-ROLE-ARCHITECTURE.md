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
- **No strict end-to-end exclusivity claim (BLOCKED):** admission/boundary serialization is
  enforced at wired OJU code paths, but a single external inference gateway/proxy is not
  implemented and is out of scope for this change. Lease expiry/renewal loss cannot interrupt
  an in-flight Ollama request and cannot prove remote termination; an expired/lost Redis lease
  by itself must not authorize a conflicting model transition, and in-flight operations may
  continue until runtime/network boundaries complete or fail. A fresh telemetry observation
  does not prove an earlier remote Ollama request has ended. Strict end-to-end exclusivity
  requires a dedicated gateway; that design is separately reviewable and is not implemented
  here. See "Lease safety and strict exclusivity" below.
- **Crash recovery:** Redis TTL expiry allows bounded recovery after worker/process crash;
  no unsafe automatic lease takeover is performed while ownership/liveness cannot be proven.
- **Model/runtime acceptance:** real dual-model runtime acceptance, vision Stage A structured
  acceptance on live model, and deployment/runtime validation are not established by source
  tests.

## Telemetry publisher (ERE Windows host)
A Windows host-side telemetry publisher is implemented at source level
(`app/host_telemetry.py`, `scripts/collect-host-telemetry.ps1`). It is NOT deployed, installed
as a persistent service, or registered as a scheduled task by this change; those require
separate owner approval (see production activation steps below).

### Collector execution
- Runs on the Windows ERE host via PowerShell (`pwsh`/`powershell`) using native WMI/CIM
  (`Win32_OperatingSystem`) for available physical memory and `nvidia-smi` for aggregate NVIDIA
  GPU total/free VRAM (capacity only; WDDM process attribution is deliberately not collected).
- Emits one JSON observation per run: UTC `collected_at`, `max_age_seconds`, `source_id`
  (`ere-host:<hostname>`), `available_ram_bytes`, `gpu_capacity_ok`, `gpu_free_bytes`,
  `gpu_total_bytes`, `measurement_status`, `collector_version`, `host_id`, a monotonic
  `sequence`, and `iso_timestamp`.
- Rejects unavailable, malformed, or contradictory readings (zero/negative RAM, GPU free >
  total, `gpu_capacity_ok=true` without a measured total, empty source id, bad sequence).
- Never fabricates GPU capacity: if `nvidia-smi` is absent or reports a failure, GPU fields are
  null and `gpu_capacity_ok=false` (measurement_status reflects the degradation).
- A durable sequence store persists the last emitted sequence so a restarted collector never
  reuses a sequence the consumer may already have accepted.

### Authenticated publication
- Each observation is serialized canonically
  (`json.dumps(sort_keys=True, separators=(",", ":"))`) and signed HMAC-SHA256 over the canonical
  payload, envelope `{"signature": ..., "payload": ...}`, matching the existing coordinator
  scheme so `read_trusted_telemetry()` verifies it unchanged.
- The signing key is supplied at publish time and is NEVER written to the telemetry file or to
  any read-only data mount.
- Publication is atomic: temp file → `fsync` → `os.replace` → least-privilege ACLs
  (Administrators full control, owning account read). Max file size (64 KiB) and strict JSON
  parsing are enforced on both write and read.

### Observation lifecycle and replay enforcement
- `read_trusted_telemetry()` reads the authorized file (`INFERENCE_TELEMETRY_FILE`) when
  configured, falling back to `INFERENCE_TELEMETRY_JSON`; unsigned env injection remains a
  test/dev-only path. HMAC, source identity (`INFERENCE_TELEMETRY_ALLOWED_SOURCE_IDS`),
  timestamp freshness (`collected_at` vs clock skew and `max_age_seconds`), and sequence/replay
  are validated before admission.
- A durable replay store (`INFERENCE_TELEMETRY_STATE_FILE`) records the last accepted sequence
  per source; an observation with `sequence <= last accepted` is rejected as
  `inference_telemetry_replay_or_rollback`, preventing rollback to an older observation across
  process restarts.
- Limits: without a nonce/one-time cache, a valid signed observation remains replayable within
  its freshness window until superseded by a higher sequence; file-based replay state is
  least-privilege but not tamper-proof against host administrators.

### Key provisioning
- The verification key (`INFERENCE_TELEMETRY_HMAC_KEY`) must be provided to the OJU service
  container through an approved secret mechanism, never as a committed Compose value. The
  signing key stays on the Windows host.

### Deployment prerequisites
- Windows host: PowerShell, `nvidia-smi` (optional; degraded without it), WMI/CIM access.
- OJU service containers (api, render-worker): read-only, least-privilege mount of the
  telemetry file; verification key via secret; `INFERENCE_TELEMETRY_FILE`,
  `INFERENCE_TELEMETRY_STATE_FILE`, `INFERENCE_TELEMETRY_ALLOWED_SOURCE_IDS`,
  `INFERENCE_TELEMETRY_HMAC_KEY`, `INFERENCE_TELEMETRY_MAX_AGE_SECONDS`,
  `INFERENCE_TELEMETRY_CLOCK_SKEW_SECONDS` configured externally.
- CPU OCR, FFmpeg, and unrelated operations remain independent of GPU telemetry availability;
  API startup does not depend on the telemetry file.

### Rollback
- Source-level: revert the commit. No containers/services are restarted, deployed, pushed, or
  merged by this change.

## Validation, rollback, and deployment boundaries
- Source validation includes unit/integration tests (mocked inference/Redis/telemetry where
  applicable), Ruff checks for changed code, repository validation, and compose config
  validation. Mocked tests are not live Ollama or deployment acceptance.
- Rollback is source-level (revert commit/changes) until deployed; no containers/services are
  restarted, deployed, pushed, or merged by this ADR change.
- OCR, Whisper/ASR, and Docling paths remain outside Ollama model-transition coordination
  except existing service/resource constraints.

