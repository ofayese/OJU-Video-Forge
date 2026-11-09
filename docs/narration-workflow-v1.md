# Source-video narration v1

## Boundary and deployment

This is a local CPU pipeline, not Wan text-to-video. It reuses PostgreSQL,
Redis/RQ's existing `video` queue, local sessions, project owner/editor roles,
private media, workflow artifacts and audit records. No cloud API or new queue
is introduced. Canonical services and media were not changed during validation.

Apply Alembic `0011` after `0010` through the normal approved deployment process;
do not apply acceptance migrations to a shared database. The optional
`compose.narration.yaml` overlay replaces the render-worker **image**, not the
queue or its volumes. Merge it after the usual Compose files (including the GPU
overlay if already used). Build `services/narration/Dockerfile` before approved
deployment. API and frontend must also be rebuilt from this revision; a successful
test against read-only source mounts does not update running services.

The worker image pins Python 3.12 Bookworm by digest, eSpeak NG
`1.51+dfsg-10+deb12u2` (GPL-3.0-or-later; bundled English voices),
FFmpeg `7:5.1.9-0+deb12u1`, Tesseract `5.3.0-2`, and English OCR data
`1:4.1.0-2`. Python ASR dependencies are hash-locked in
`requirements-narration.lock.txt`: faster-whisper `1.2.1` and CTranslate2
`4.6.0`. Packages imported successfully in the built Linux container.
No Windows runtime dependencies were installed.

### ASR model authorization gate

No ASR model was downloaded or inferred during this implementation.
Proposed source: `Systran/faster-whisper-tiny` (MIT), revision
`d90ca5fe260221311c53c58e660288d3deb8d356`, at
<https://huggingface.co/Systran/faster-whisper-tiny/tree/d90ca5fe260221311c53c58e660288d3deb8d356>.

| File | Bytes | Published identity |
|---|---:|---|
| model.bin | 75,538,270 | SHA-256 `dcb76c6586fc06cbdac6dd21f14cfd129cc4cdd9dce19bf4ffa62e59cbe6e6d1` |
| config.json | 2,249 | Git blob `3baa18e2b321a2f489614607852a729fcd516480` |
| tokenizer.json | 2,203,239 | Git blob `7818adb6de9fa3064d3ff81226fdd675be1f6344` |
| vocabulary.txt | 459,861 | Git blob `c9074644d9d1205686f16d411564729461324b75` |
| Total | 78,203,619 | Pinned repository revision above |

Owner approval is required before transfer to the private Compose
`narration-models` volume at `/models/asr/faster-whisper-tiny`. Non-LFS SHA-256
values must be recorded after the approved transfer; Git blob IDs above are not
SHA-256 checksums. The worker verifies the published model.bin SHA-256, loads
local files only, and records checksums of every model file. Hugging Face offline
mode and telemetry disablement are set in the worker image/overlay.
ASR always runs CPU int8 with two threads and VAD. Empty speech results remain
empty; missing model/runtime and failed inference never become transcripts.

### Optional visual understanding

Visual analysis is an enhancement, not a prerequisite for narration. Unavailable
OCR/vision, media-analysis errors and local vision request failures are recorded
as an explicit `scene_analysis.status: unavailable` warning and audited. They do
not discard a successful transcript or block manual script authoring/approval.
Cancellation still stops the job; ASR failures are not converted into successful
transcripts.

OCR is local Tesseract, not scene/action understanding. At most 12 timestamped
frames are sampled over a maximum 600-second source. Sampled grayscale change
detection identifies major visual changes, not exact mouse actions or a complete
scene-cut timeline. At most six frames may be sent to a configured local Ollama
vision model with explicit UI opt-in. Set `NARRATION_VISION_URL` to an approved
loopback/`host.docker.internal`/`ollama` HTTP endpoint and
`NARRATION_VISION_MODEL` to an approved installed model. Public endpoints,
redirects and environment HTTP proxies are not used. Capability is checked before
inference; the model is requested with `num_gpu=0`, two threads, bounded output
and no keep-alive. No inference service is exposed publicly or reconfigured.
Interpretations are explicitly uncertain and require human review. Real VLM
interpretation was not exercised; installed-model metadata advertised vision.

## API and review

1. `POST /v1/projects/{project}/narrations` with `Idempotency-Key` and
   `{"media_id":"uploaded-video-id","options":{"analysis_mode":"manual"}}`.
   Use `analysis_mode: transcript` for actual ASR, or `manual` to explicitly skip
   it. Optional `vision: true` invokes configured local visual understanding.
2. `GET /v1/projects/{project}/narrations/{id}` returns project/source/job identity,
   analysis, stage, current script revision and authorized artifact metadata.
3. `PUT .../{id}/script` with `expected_revision` and `segments` appends a revision.
   Segments have `id`, `start_ms`, `end_ms`, `text`, `evidence`, `uncertainty` and
   `estimated_ms`. IDs/evidence are bounded; timing must be ordered,
   nonoverlapping and within source duration. `manual` is an explicit human
   evidence reference. Edits invalidate approval; prior revisions remain stored.
4. `POST .../{id}/approval` with `expected_revision` and `decision` (`approved` or
   `rejected`). Owner/editor membership is required and approval is audited.
5. `POST .../{id}/voice-preview` produces a fixed sample sentence, **not** the
   final script voiceover. Final synthesis still requires script approval.
6. `POST .../{id}/render` with `expected_revision`, `voice` (`en-us`/`en-gb`),
   `rate` (100-220 words/minute), `audio_mode` (`replace`/`mix`/`preserve`),
   `original_volume` (0-100) and `narration_gain` (0-150).
7. Cancel through the existing `/v1/jobs/{job}/cancel` endpoint. Only failed
   eligible stages may use `POST .../{id}/retry`, at most twice. Interrupted
   workers fail through existing recovery; retry creates a new audited job.

Database job statuses remain compatible. Detailed phases are stored on the
narration record and audited: queued, transcribing, analyzing_scenes,
drafting_script, awaiting_script_approval, synthesizing_audio, mixing_audio,
verifying_artifact, awaiting_review, failed, cancelled. An analysis job ending at
the script gate has no completed video. A separate render job binds the approved
revision. Both requester and approver membership are checked before rendering.

Drafts use ASR hypotheses verbatim with nearby frame references, or uncertain VLM
observations when no speech draft exists. Instructional/documentary/concise
styles and editorial instructions are stored for review; there is no claim of
unconstrained prose generation or translation. English synthesis is supported.
Blank/no-speech drafts require manual authorship, not invented source events.

## Rendering and retrieval

The script is synthesized segment-by-segment using local eSpeak NG, with
silence inserted to align timestamps and loudness normalization. Robotic
synthetic voices are expected; no real-person cloning is performed. Speech that
exceeds its window fails with `timing_revision_required`, without truncation or
speed-up. Edit and reapprove before rendering again.

Source video is stream-copied to a new MP4, preserving geometry, aspect ratio,
frame rate, intended duration and visual packet hash. Unsupported MP4 codecs
fail rather than silently transcode/crop. Replace uses narration; mix applies
explicit original/narration gain and limiting; preserve stream-copies original
audio unchanged and does not synthesize speech. Final FFprobe and visual hash
checks precede artifact registration. Original source checksums are rechecked.

Private artifacts include analysis JSON, transcript TXT/SRT/VTT, narration WAV,
narration SRT/VTT, final MP4 and provenance JSON. Use the existing authorized
project-artifact endpoint. Provenance binds source/project/job, approved revision,
script hash, engine versions, voice/settings, output checksum and media metadata.
Frontend previews use only current narration/job/revision artifacts and revoke
object URLs on new jobs, source/project/operation changes and logout. Historical
outputs are never guessed as the latest narration.

## Acceptance evidence (2026-11-09)

One isolated real acceptance used a synthetic 90-second blue/green/red tutorial.
It used actual local login, upload, RQ dispatch/worker, SQLite test persistence,
disposable Redis, OCR, script editing/approval, eSpeak NG, FFmpeg and authorized
retrieval. No inference provider was mocked. Neither PostgreSQL-specific
narration locking nor browser interaction was validated by this acceptance.

- Mode: **manual script**, ASR **NOT_VERIFIED: model download not authorized**.
- Nine timestamped frames; real OCR recognized synthetic title; visual changes
  detected. Three manually verified script segments, approved revision 2.
- Source and derived: **90.000 seconds, 640x360, SAR 1:1, H.264, 12 fps**.
- Real eSpeak NG English-US speech mixed with original synthetic audio; narration
  WAV and derived MP4 registered and authorized download returned HTTP 200.
- Source bytes unchanged; source/derived compressed visual stream hashes matched.
- Output: **224,751 bytes**; SHA-256
  `3c9dffe60e00418c223622d94d33143e2551b19401c87475007fcee889c4f2b3`.
- Command inside the disposable narration container:
  `python accept_narration.py` (read-only mount of `scripts/accept_narration.py`,
  current app mount, private temporary SQLite/storage, disposable Redis on an
  internal test network): exit 0.
- One targeted acceptance retry exercised a deliberately unavailable media-tool
  PATH, a failed voice-preview job, and its eligible retry. Approval state was
  restored and a real voice-preview WAV was retrieved before final rendering.
  The resulting MP4 had the same byte count and checksum above. This verified
  the correction preserving the preview job's return stage on retry.

### Final bounded validation

Commands ran in Linux Docker containers on the Windows development host; no
application dependencies were installed on Windows. The API test image was
`oju-video-forge-api-test:phase4c`, with current source mounted read-only and
network disabled for tests. The frontend image was
`oju-video-forge-frontend-build:phase4b`, with current frontend source mounted
read-only and network disabled.

| Exact command inside the container | Exit | Actual result |
|---|---:|---|
| `python -m pytest -q -p no:cacheprovider tests/test_narration.py tests/test_narration_media.py tests/test_api_gpu.py tests/test_review.py` | 0 | 109 passed, 5 deprecation warnings |
| `ruff check app tests alembic scripts` | 0 | Passed |
| `npm run typecheck && npm test -- src/api/client.test.ts src/ui/App.test.tsx src/ui/NarrationWorkspace.test.tsx && npm run build` | 0 | Type checking, 38 tests, production build passed |
| `python -m pytest -q -p no:cacheprovider` | 0 | 265 passed, 12 skipped, 5 deprecation warnings |
| `python scripts/validate_repository.py` | 0 | YAML, metadata, action pins and environment-template parity passed |
| `python scripts/validate_acceptance_register.py` | 0 | Existing acceptance register schema passed; historical claims unchanged |
| `python scripts/export_openapi.py --output /tmp/narration-openapi.json` | 0 | Actual updated API schema exported |
| `pip-audit -r requirements-narration.lock.txt` | 1 | Alpine audit environment could not resolve a CTranslate2 musl wheel |
| `pip-audit --disable-pip --no-deps -r requirements-narration.lock.txt` | 0 | All fully pinned, hash-locked optional dependency metadata audited; no known vulnerabilities |

The 12 skips require disposable PostgreSQL `*_test` and Redis database 15;
canonical services were deliberately not used. Warnings are the existing
Starlette/httpx and Python 3.12 SQLite datetime-adapter deprecations.
The single full regression preceded the two-line preview retry correction;
the real targeted acceptance above verified that final correction.
The optional narration image was rebuilt successfully with the final code.
`docker compose --env-file .env.development.example -f compose.yaml -f compose.narration.yaml config --quiet`
also exited 0 without starting services.

No Phase 5 GitHub CI, CodeQL, image vulnerability scan or browser acceptance pass
is claimed. A PR stacked on the still-open safeguard branch does not match the
existing `main`-only pull-request CI filters. PostgreSQL-specific migration and
locking acceptance, automatic ASR, browser interaction and approved canonical
deployment remain outstanding. Local VLM inference is a separate optional
acceptance criterion.

The canonical database, Docker services, Wan model volume, private recordings,
backups and generated media were not changed. The disposable acceptance
containers/network and synthetic media were removed. Initial transcript-based
narration acceptance is **NOT_VERIFIED** until the model gate and real ASR
verification are completed. Visual understanding is not a prerequisite.
This evidence does not supersede the outstanding browser gate.

## Initial narration release acceptance

The initial release requires real local transcription when speech exists,
editable timestamped scripts, human approval, local TTS, synchronized
source-preserving MP4 rendering, FFprobe/SHA-256 verification, authorized
retrieval and functional frontend controls. Videos without speech may use
user-authored or user-corrected scripts. No visual observations or technical
procedures may be invented.

OCR and automated visual interpretation have separate optional acceptance
criteria. OCR has real synthetic acceptance evidence above; automated local
vision inference remains **NOT_VERIFIED**. Neither is an initial narration
release blocker, and no new vision models/infrastructure are required.

The verified manual-script path remains valid. Initial speech-based narration
acceptance is still **NOT_VERIFIED**, pending approved Whisper model transfer
and real transcription validation. The separate browser, authorization and
deployment limitations above are unchanged.

### Optional-visual isolation follow-up validation

The priority clarification above adds focused worker regressions for missing
vision, a vision request timeout, failed OCR in manual mode, speech-free sources,
and cancellation. These use synthetic storage and mocked provider outcomes;
they verify failure isolation, not real transcription or vision inference.

- `python -m pytest -q -p no:cacheprovider tests/test_narration.py -k optional_visual_failure`:
  initial exit 1 because the new fixture used an invalid private-storage key.
  Correcting the fixture to use the unchanged private-storage helper produced
  exit 0: **5 passed, 12 deselected**, one existing Starlette/httpx warning.
- `ruff check app/narration_jobs.py tests/test_narration.py`: exit 0.
- `npm run typecheck && npm test -- src/ui/NarrationWorkspace.test.tsx`:
  exit 0; type checking and **4 tests passed**.

Checks used the same Linux Docker test/build images and read-only source mounts
described above, with networking disabled. No full regression, real inference,
model transfer, image rebuild or canonical service change was repeated.
