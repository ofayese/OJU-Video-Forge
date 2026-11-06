# ERE Render Validation

**Status:** NOT VERIFIED

The repository audit found a deterministic FFmpeg test-clip path in `app/worker.py`, but no end-to-end media upload, timeline composition, render job, or artifact retrieval was executed.

Neither `ffmpeg` nor `ffprobe` was found on the Windows host `PATH`. Container availability was not tested because no application service was started.

A standalone renderer and timeline v1 contract were added in `app/rendering.py` and `docs/rendering-contract-v1.md`. The focused command `python -m pytest -q -p no:cacheprovider tests/test_rendering.py tests/test_contract.py` produced **60 passed, 1 failed, 1 skipped**. The failing assertion expected `b"ok\n"` from a Windows Python subprocess but received `b"ok\r\n"`. The test failure was not repaired. A later request to run the full suite was denied and was not retried.

| Acceptance step | Result |
|---|---|
| Authenticate and create a project | Not run |
| Upload and inspect sample media | Not run |
| Create timeline with two clips/title/audio/captions | Not run |
| Submit and monitor render | Not run |
| Produce playable MP4 | Not run |
| Validate with FFprobe | Not run |
| Generate and verify SHA-256 | Not run |
| Retrieve artifact through authorized API | Not run |
| Verify audit trail | Not run |

The reported `python -m pytest -q` result of 2 passed is not evidence of this workflow. No render artifact or evidence bundle is present from this validation.
