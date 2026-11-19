# OJU Video Forge — Narration and Rendering Quality Report

**Branch:** `feat/silent-recording-training`
**Date:** 2026-11-19

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

---

## H.264 Evaluation

| Check | Result |
|---|---|
| libx264 encoder available (API container) | Yes |
| libx264 encoder available (render-worker) | Yes |
| Test encode successful | Yes (320×240, 10fps, 1s) |
| H.264 as production default | No — not yet regression-tested |

**Recommendation:** H.264 can be produced with the current qualified image. Do not change the
production rendering default until H.264 output passes the full regression test suite.

---

## Future Quality Improvements (Backlog)

1. Evaluate alternative offline TTS providers (Piper, Coqui TTS) for improved intelligibility
2. Add silence trimming and loudness normalization (FFmpeg filters)
3. Add audio fades at segment boundaries
4. Add subtitle/caption synchronization verification
5. Support H.264 as an optional output profile
6. Formal listening tests for speech intelligibility
