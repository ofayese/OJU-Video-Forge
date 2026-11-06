# Standalone rendering contract v1

`app.rendering.RenderEngine(media_root, ffmpeg=..., ffprobe=..., font=...)`
accepts JSON text through `render(payload)` and returns a `RenderResult` containing
the generated MP4 path, byte size, SHA-256, duration, dimensions, frame rate and
codec names. It imports no API, database, worker, or network client.
`RenderResult.provenance` also records the canonical timeline SHA-256, SHA-256 of
each resolved input and configured font, preset name and version, FFmpeg and
FFprobe version lines and full version-output hashes, and output SHA-256.

```json
{
  "version": 1,
  "clips": [
    {"path": "media/first.mp4", "in_ms": 0, "out_ms": 1000},
    {"path": "media/second.mp4", "in_ms": 1000, "out_ms": 2000}
  ],
  "title": {"text": "Example title", "start_ms": 0, "end_ms": 1000},
  "captions": [
    {"text": "Example caption", "start_ms": 1000, "end_ms": 2000}
  ],
  "audio": {"path": "media/music.wav", "in_ms": 0, "volume_percent": 70},
  "output": {"profile": "preview", "format": "mp4"}
}
```

## Semantics and validation

- JSON v1 only; unknown/duplicate fields, boolean numbers, unsupported options and
  nonfinite numbers are rejected. Maximum JSON size: 64 KiB.
- 1-16 ordered video clips; MP4/MOV/MKV/WebM containers only. Integer milliseconds,
  `0 <= in_ms < out_ms <= 120000`; total length <=120 seconds. Clip boundaries must
  align to output frames (e.g. 1000 ms works for both profiles).
- Profiles: `preview` = 640x360 at 24 fps; `hd` = 1280x720 at 30 fps. Clips are
  resized with preserved aspect ratio and black padding; square pixels, H.264,
  yuv420p, MP4. No arbitrary codecs, filters, encoder arguments or destination.
- Original clip audio is intentionally discarded. Optional single WAV/MP3 audio
  starts at timeline zero after `in_ms`, volume 0-100 percent; silence pads short
  audio, long audio is trimmed. Output audio is stereo AAC at 48 kHz.
- Optional title and up to 100 ordered non-overlapping captions use absolute
  timeline milliseconds, half-open `[start_ms, end_ms)` intervals, bounded by total
  duration. Text is 1-200 printable Unicode characters, single-line and literal.
  Fixed white text/black box: title near top, captions near bottom. A local
  administrator-configured TTF/OTF font **under media root** is required for text;
  no font auto-download or fallback contract. Font location permits only portable
  letters/digits/underscore/space/slash/dot/hyphen and a Windows drive colon.
- Input paths are portable relative paths under the configured local media root;
  absolute, drive, UNC, URL, backslash, ADS, traversal, missing/empty files, and
  symlink escapes are rejected. Each input <=512 MiB; video <=4096x2160; audio
  <=8 channels / 192 kHz. Input metadata and trim range are probed before encoding.

## Execution boundary and remaining operational requirements

- Executables are resolved locally from trusted administrator configuration;
  Windows command/batch wrappers and remote executables are rejected.
- All subprocess calls use argument lists and `shell=False`, no stdin, discarded
  stderr, bounded stdout (64 KiB), a private generated work directory, sanitized
  environment, file-only protocols, explicit demuxers, and a shared deadline
  (default 180 seconds, configurable 1-600; each probe <=15 seconds).
- Single codec/filter threads, 64 MiB maximum individual FFmpeg allocation,
  bounded probing, fixed resolutions/duration, and `-fs` 128 MiB constrain work.
  Output must be strictly below 128 MiB and match probed MP4/profile/codecs/duration.
  `-fs` can overshoot by mux overhead before post-validation; these are **not**
  an OS-enforced total RSS/CPU/disk quota. Deployment must add process/container
  memory, CPU and storage quotas. No existing infrastructure is changed here.
- Root, media, tools and font must be privately owned and immutable during a render;
  POSIX scratch mode is 0700, Windows privacy depends on the root's inherited ACL.
  Path validation is not a sandbox against concurrent hostile filesystem mutation
  or vulnerabilities in native decoders. Do not use this with shared writable roots.
- Success retains only a new `render-*/output.mp4`; failure removes only that
  renderer-owned scratch directory. Inputs are never copied or modified.
- Encoder settings, timestamps, metadata removal, threads and CPU feature settings
  are fixed. Byte determinism targets identical media/font and identical pinned
  FFmpeg/codec builds, not cross-version/cross-platform equality.
- Inputs are hashed before and after rendering; a changed input fails the render.
  This detects changes but does not replace the required immutable private storage.

## Trusted worker interface

The existing `render_test` interface remains unchanged. A trusted staging service
may persist a job with operation `render_timeline_v1`, a canonical UUID job ID,
and `spec={"timeline": <v1 object>}` before dispatching `app.worker.execute`.
It must create a private `<artifact root>/<job ID>/inputs` directory containing
only media authorized for that job. Timeline paths are relative to that directory;
an optional administrator-staged `font.ttf` in it enables text. The staging
staged files must remain immutable and inaccessible to other users throughout
execution; the directory must allow the renderer to create its own workspace.
The worker atomically claims queued or dispatch-pending jobs,
records `running`, and on success saves `provenance.json` beside `inputs`, points
the job artifact at the verified MP4, and records `awaiting_review`. Validation or
worker errors produce a `failed` audit event and a bounded, non-sensitive error.
Only one delivery can claim a job; later deliveries of terminal jobs do nothing.

This is an internal interface, not a public upload or timeline submission API.
The existing API cannot stage or authorize media; it must not be extended to accept
timeline paths until project/media authorization and immutable staging exist.
A process crash can leave `running` jobs or unregistered output; durable dispatch,
recovery, cancellation, provenance access control, and filesystem quotas remain
dependencies, not guarantees of this interface.

## Verification and integration

`python -m pytest -q tests/test_rendering.py` tests validation, safe argument
construction, subprocess bounds, mocked metadata/checksum and cleanup.
`test_real_fixture_render_is_repeatable` generates local synthetic video/audio and
checks two real renders **only when local FFmpeg and FFprobe are available**.
Mocks and a skipped fixture test do not establish actual media rendering.

Remaining integration: authenticated media storage/ownership checks, timeline
persistence/revisions, durable worker dispatch/cancellation and recovery, artifact
registration/authorized retrieval/audit trail, deployment quotas, font/build pinning,
and API/UI acceptance workflow. The existing render-validation acceptance register
is intentionally unchanged; this module is not evidence of that workflow.
