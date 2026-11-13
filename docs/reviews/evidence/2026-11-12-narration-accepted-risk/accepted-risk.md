# Narration/base-image unfixed CVE accepted risk

**Assessment date:** 2026-11-12
**Scanner:** Trivy 0.76.2 with vulnerability DB updated 2026-11-12
**Branch:** `feat/docling-rapidocr-narration` (PR #61)
**CI security gate:** `--exit-code 1 --severity HIGH,CRITICAL` (no `--ignore-unfixed`) — **unchanged, not weakened**

## Decision

Residual HIGH/CRITICAL findings on the narration and docling-ocr images are **accepted as deferred upstream risk**, not remediated. The PR remains in **draft**; routine deployment is blocked until supported package fixes appear in Debian repos or an owner-approved remediation plan is reviewed.

This is not a concession to the scanner gate. The gate still fails on these findings; acceptance is a separate owner decision documented here, not a CI policy change.

## Affected images

| Image | Digest | OS | CRITICAL | HIGH | Unfixed | Fixed |
|---|---|---|---:|---:|---:|---:|
| `ovf-narration:ci` | `sha256:b6e6ef2ddfa59ee938ff6b01a844776381ac87065dc865e4486971710ff02e34` | Debian 12 (bookworm) | 8 | 300 | 308 | 0 |
| `ovf-docling:ci` | `sha256:237c1324b0cff6c42c16ef5b2cf8f09d4578d3f1154eed24231c857327f124e9` | Debian 12 (bookworm) | 9 | 149 | 158 | 39 |

Scanner output: `docs/reviews/evidence/2026-11-12-narration-accepted-risk/ovf-narration.trivy.json` and `ovf-docling.trivy.json` (raw, local-only). SBOMs: `ovf-narration.sbom.cdx.json`, `ovf-docling.sbom.cdx.json` (CycloneDX).

## Root cause

The large majority of unfixed findings are **OS shared-library packages**, not application code:

- `libxml2`, `zlib1g`, `libglib2.0-0`, `libmbedcrypto7`, `libsqlite3-0`, `libaom3`, `libexpat1`, `libtiff6`, `libx11-6`, `perl-base`, `util-linux`, `bsdutils`, `gzip`, `libgssapi-krb5-2`/krb5 family
- **FFmpeg shared libraries** (`libavcodec59`, `libavformat59`, `libavfilter8`, `libavdevice59`, `libavutil57`, `libswresample4`, `libswscale6`, `libpostproc56`): 108 unfixed HIGH on bookworm

Debian's security tracker shows **no fixed package version in bookworm** for this set; many are `postponed` or `<no DSA>`. Forky/Sid fixes exist for several but have not been backported to bookworm or trixie-backports.

## Base-image replacement evaluated and rejected

| Alternative | Result |
|---|---|
| **Debian 13 TriXie** (`python:3.12-slim-trixie`) | CRITICAL 8→1, HIGH 300→281 (−26). FFmpeg CVEs **remain unfixed** (FFmpeg 7.1.5 on trixie: 102 unfixed HIGH across `libav*` packages vs 108 on bookworm's 5.1.9). Only an 8.4% reduction. FFmpeg change 5.1.9→7.1.5 requires full re-verification of H.264/AAC, filters, hw accel, faster-whisper, eSpeak, MP4. |
| **Trixie docling** | `libgl1-mesa-glx` has no installation candidate in trixie slim — does not build. |
| **Other distros** (Ubuntu 24.04, Alpine, distroless, Chainguard) | Same root cause: no stable repo ships a patched FFmpeg package yet. Would trade one unpatched FFmpeg CVE set for another, plus require full media-pipeline re-verification. |

## Media-framework replacement evaluated and rejected

`app/narration.py` invokes `ffmpeg` directly at 11 call sites (lines 174–688): audio extraction, frame extraction, pixel analysis, silent-audio generation (lavfi), concat demuxer, and MP4 muxing with `amerge`/`aresample` filters plus H.264/AAC encode.

- **PyAV**: binds to the same `libav*` shared libraries — identical CVE set.
- **GStreamer / OpenCV / moviepy / imageio**: require a complete rewrite of the media pipeline; introduce their own CVE sets; break filter-equivalence acceptance (scale, overlay, amerge, amix, aresample); would weaken functional acceptance rather than remediate.

No alternative eliminates the FFmpeg CVE exposure without a substantial, unverified rewrite.

## Narrative functionality preserved (verified)

All narration acceptance criteria pass on the bookworm image (`sha256:b6e6ef2d…`):

- H.264 decode/encode + AAC decode/encode (`libx264`, `aac` encoders)
- Audio preservation, replacement, and mixing (`amerge`, `amix`, `aresample` filters)
- Video stream preservation (`-map 0:v:0`, `-map 0:a:0`)
- Required filters: `scale`, `amerge`, `amix`, `aresample`
- faster-whisper CPU-int8 transcription (imports; model load requires volume-mounted model file, offline `HF_HUB_OFFLINE=1`)
- eSpeak NG 1.51 synthesis + FFmpeg audio processing
- Script approval and final MP4 generation
- Source-file integrity and project authorization

Tests: `tests/test_narration.py` — **40 passed**. Ruff lint: **clean**. Trivy config scan: **0 misconfigurations** (both Dockerfiles have non-root `USER`).

## Residual risk summary

All 308 narration findings and all 158 docling findings (120 unfixed) carry **no fixed version** in Debian bookworm. Fixes depend on upstream FFmpeg and Debian packaging, outside this project's control. No package upgrade, base-image swap, or media-framework change available today reduces the exposure materially without accepting functional or build risk.

## Reassessment triggers

Revisit this accepted-risk decision when any of the following occurs:

- Debian publishes a fixed `ffmpeg` package in bookworm or trixie-backports covering the assessed `libav*` CVEs (see [Debian FFmpeg tracker](https://security-tracker.debian.org/tracker/source-package/ffmpeg)).
- A TriXie build of the docling image resolves `libgl1-mesa-glx` availability in slim and the FFmpeg CVE count drops materially.
- An owner-approved parser/transcoding isolation boundary (separate short-lived container, no credentials/network) is separately validated.
- The CI security gate policy is changed by owner decision (this document does not change it).

Until then: PR #61 stays in **draft**, CI gate unchanged, no deployment.
