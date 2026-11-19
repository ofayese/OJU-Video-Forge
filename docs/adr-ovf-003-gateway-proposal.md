# OJU Video Forge — ADR-OVF-003 Execution Gateway Implementation Proposal

**Status:** BLOCKED (ADR remains Proposed)
**Branch:** `feat/silent-recording-training`
**Prerequisite:** Separate architectural authorization required

---

## Safety Gap

The current Redis lease coordination serializes heavy-inference admission across processes,
but **cannot interrupt an in-flight Ollama HTTP request**. If the lease is lost while Ollama
is processing a request:

1. The request continues to completion
2. The caller cannot prove remote runtime cancellation
3. A new model transition could proceed while the old request is still active

The lease code documents this limitation explicitly. Ordinary Redis lease serialization
is **not** strict remote-inference exclusivity.

---

## Proposed Execution Gateway Scope

A separately reviewable execution gateway component would provide:

### In-flight Request Tracking
- Assign generation/fencing tokens to each model request
- Track active requests in a durable registry
- Correlate lease ownership with in-flight requests

### Model-Transition Quiescence
- Before model transition, wait for all in-flight requests to complete or time out
- Reject new requests during quiescence window
- Bounded timeout for quiescence (fail closed if requests don't complete)

### Lease-Loss Handling
- On lease loss, signal all in-flight requests to cancel
- Wait for cancellation acknowledgment or timeout
- Prevent new model transitions until lease is re-acquired

### Cancellation Acknowledgment
- Ollama client supports cancellation via request context
- Gateway tracks cancellation status per request
- Fail closed if cancellation is not acknowledged within bound

### Queue Coordination
- FIFO queue for heavy-inference requests
- Priority handling for time-sensitive operations
- Backpressure when queue is full

### Audit Events
- Lease acquire/release/lost
- Request start/complete/cancel
- Model transition begin/complete/abort
- Quiescence enter/exit

### Acceptance Tests
- Lease loss during active request → cancellation acknowledged
- Model transition blocked during active request
- Quiescence timeout → fail closed
- Concurrent requests serialized correctly

---

## Relationship to ADR-OVF-003

- ADR-OVF-003 (DUAL-ROLE-ARCHITECTURE.md) is **Proposed**, not Accepted
- Strict end-to-end inference exclusivity remains **BLOCKED** without owner authorization
- This proposal is linked to ADR-OVF-003 but does not implement it
- No execution gateway code is written in this phase
