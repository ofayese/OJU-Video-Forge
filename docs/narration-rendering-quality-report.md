# OJU Video Forge — Narration and Rendering Quality Report

**Branch:** `main`
**Date:** 2026-11-20

---

## Current TTS Provider: espeak-ng 1.52.0

| Attribute | Value |
|---|---|
| License | GPL-3.0-or-later |
| Voice | en-us |
| Rate | 160 wpm |
| Sample rate | 22050 Hz |
| Channels | mono |
| Codec | PCM signed 16-bit little-endian |
| Output | narration.wav (2.2MB, 49.98s) |

**Assessment:** Functional offline baseline. No external account or hosted API required.
CPU-only, lightweight. Compatible with existing narration pipeline.

### Quality Considerations (not yet addressed)
- Voice selection: Only en-us tested; en-gb available
- Speech intelligibility: Not formally evaluated
- Pronunciation: Not formally evaluated
- Narration pacing: Fixed rate; no dynamic adjustment
- Silence trimming: Not applied
- Loudness normalization: Not applied
- Audio fades: Not applied
- Segment transitions: Direct concatenation

---

## Current Rendering Output

| Attribute | Value |
|---|---|
| Container | MP4 (QuickTime / MOV) |
| Video codec | MPEG-4 Part 2 |
| Video resolution | 640×360 |
| Video frame rate | 10 fps |
| Video duration | 50.000s |
| Audio codec | AAC LC |
| Audio sample rate | 22050 Hz |
| Audio channels | mono |
| Audio duration | 49.982s |
| Total bitrate | ~52.9 kbps |
| Output size | 330,516 bytes |
| SHA256 | bc02763411a2e200c60b64a132bccb281563cbb48d13f9f2bab05269b7043da2 |

**Audio verification:** Non-silent — AAC audio stream present with substantial bitrate (17.1 kbps).
Narration audio is synchronized with video.

**Rendering pipeline:** `media.mux()` in `app/narration.py:837` performs lossless MP4 muxing.
By default it copies the source video codec (`-c:v copy`); H.264 re-encode via the available
H.264 encoder is only applied when `render_annotations=True` (drawtext annotations) or when
`render_profile: "h264_aac"` is explicitly requested. Audio is re-encoded to AAC at 128k
(mix mode) or copied (preserve mode). Output uses `-movflags +faststart`.
Source video codec must be H.264, HEVC, AV1, or MPEG-4 Part 2.

---

## H.264 Evaluation

| Check | Result |
|---|---|
| H.264 encoder available (API container) | Yes — `libopenh264` (libx264 is disabled in this ffmpeg build) |
| H.264 encoder available (render-worker) | Yes — `libopenh264` |
| Test encode successful | Yes (320×240, 10fps, 1s, libopenh264) |
| Qualified H.264 output via `media.mux()` | Yes — direct `media.mux()` execution with `render_annotations=True`, artifact SHA256 `e374d461d6aeeba2916578c4dcceefd16770436436477c1b44216c53a946a409`, 103,619 bytes |
| H.264 as production default | PARTIAL — production render job `047f2e0f` used `-c:v copy` (MPEG-4 Part 2 passthrough); H.264 not enforced by application-level render endpoint |

**Status:** Direct `media.mux()` H.264/AAC encoding is qualified using `libopenh264`. Application-level H.264 profile enforcement is PARTIAL: the production render endpoint copies the source codec unless `render_profile: "h264_aac"` is explicitly requested. Full regression test suite passed (465 passed, 16 skipped).

---

## Three Representative Training Cases

| Case | Description | Schema Support | Status |
|---|---|---|---|
| Case A — Simple linear procedure | 3-5 sequential steps | Fully supported | BLOCKED at human review |
| Case B — Branching procedure | Decision point with two alternative paths | Not supported by TrainingDraft schema | BLOCKED — schema limitation |
| Case C — Longer multi-step procedure | 8+ distinct steps | Fully supported | BLOCKED at human review |

**Human review requirement:** Actual approval decisions must be made by an authorized human. The agent prepared content and submitted review requests but must not impersonate a human reviewer or self-approve generated material. All three cases are BLOCKED at the human review boundary; downstream acceptance (render, final-video review, artifact retrieval) cannot proceed without human approval.

---

## Evaluation Rubric

Scores are 1–5. Human ratings are `PENDING_HUMAN_EVALUATION` until an authorized reviewer provides them.

| Dimension | Evaluation criteria |
|---|---|
| Procedural accuracy | Steps match the approved source evidence |
| Completeness | No required steps missing |
| Evidence traceability | Every material instruction is attributable |
| Instructional clarity | Understandable objectives and directions |
| Branching correctness | Decision paths represented faithfully |
| Narration intelligibility | Speech is understandable and appropriately paced |
| Audio/video synchronization | Narration matches the appropriate scene |
| Visual readability | Text and annotations are legible |
| Reviewer usability | Reviewer can identify and correct defects |
| Output compatibility | Video can be decoded and inspected reliably |

Objective measurements (codec, duration, audio, synchronization) are captured via FFprobe and stored separately from subjective human ratings.

---

## Observed Defects

| Severity | Reproduction | Affected | Expected | Actual | Correction | Verification |
|---|---|---|---|---|---|---|
| High | Documentation claims Phase 13 artifact is H.264 | docs/ | MPEG-4 Part 2 | Documented as H.264 | Corrected to MPEG-4 Part 2 | FFprobe verified |
| High | `mux()` hardcodes `libx264` encoder | app/narration.py | Uses available H.264 encoder | libx264 disabled in build | Added `h264_encoder()` detection | Unit tests pass |
| Medium | No explicit render profile selection | app/api.py, app/narration.py | `render_profile` field | Only `render_annotations` bool | Added `render_profile: source_passthrough \| h264_aac` | Unit + API tests pass |

---

## Future Quality Improvements (Backlog)

1. Evaluate alternative offline TTS providers (Piper, Coqui TTS) for improved intelligibility
2. Add silence trimming and loudness normalization (FFmpeg filters)
3. Add audio fades at segment boundaries
4. Add subtitle/caption synchronization verification
5. Formal listening tests for speech intelligibility
6. Extend TrainingDraft schema to support branching procedures (Case B)
