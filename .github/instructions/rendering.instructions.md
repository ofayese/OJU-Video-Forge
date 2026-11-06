---
name: Deterministic Rendering
description: "Use for FFmpeg, FFprobe, media storage, timeline contracts, render workers, and artifact generation."
applyTo: "app/rendering.py, app/worker.py, tests/test_rendering.py, docs/rendering-contract-v1.md"
---

# Rendering guidance

- Keep CPU rendering deterministic and bounded before adding GPU inference.
- Preserve the versioned timeline contract, supported presets, and allowlisted
  formats. Treat all timeline fields and media as untrusted.
- Use argument arrays with `shell=False`, private per-job workspaces, fixed
  executables, timeouts, output-size limits, FFprobe validation, and SHA-256 evidence.
- Do not treat a mocked probe, skipped fixture, or ComfyUI prompt submission as a
  verified MP4 render.
- Never overwrite or delete user media to clean up temporary renderer-owned files.
- Keep GPU/model loading outside the deterministic CPU render path.
