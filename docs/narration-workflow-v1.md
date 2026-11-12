# Source-video narration v1

## Boundary and deployment

This is a local CPU pipeline, not Wan text-to-video. It reuses PostgreSQL,
Redis/RQ's existing `video` queue, local sessions, project owner/editor roles,
private media, workflow artifacts and audit records. No cloud API or new queue
is introduced. Canonical services and media were not changed during validation.

Apply Alembic `0011` after `0010` through the normal approved deployment process;
do not apply acceptance migrations to a shared database. The optional
`compose.narration.yaml` overlay replaces the render-worker **image**, not the
queue or its volumes. It adds `docling-ocr` to the canonical `oju-video-forge`
project on an internal network shared with the render worker. The overlay is
not a standalone Compose file: use it after `compose.yaml`. API, frontend and
worker images must contain the selected revision; tests against source mounts do
not update running services.

### Canonical narration lifecycle

After separate deployment approval and verification of the private `.env`,
select the narration overlay for every lifecycle command:

```powershell
.\scripts\forge.ps1 -Environment application -Narration -Plan
.\scripts\forge.ps1 -Environment application -Narration config --quiet
.\scripts\forge.ps1 -Environment application -Narration up -d --wait
.\scripts\forge.ps1 -Environment application -Narration ps
.\scripts\forge.ps1 -Environment application -Narration logs --tail 50 docling-ocr
.\scripts\forge.ps1 -Environment application -Narration restart docling-ocr
.\scripts\forge.ps1 -Environment application -Narration stop
```

The Bash equivalent uses `bash scripts/forge.sh application --narration`
before the Compose operation. The helpers select `compose.yaml` followed by
`compose.narration.yaml` under project `oju-video-forge`; omitting the option
does not manage Docling. The TCP health check shows only that port 9623 accepts
connections, not that OCR works or that a 120-second end-to-end deadline is
enforced. Use an authorized synthetic end-to-end smoke to verify actual OCR.

The CPU image is `oju-video-forge/docling-ocr:1.37.0`. Verify its image ID
against the approved local image before startup; the historical
`oju-video-forge-docling-ocr:1.37.0` tag is retained for rollback. A local
image ID is not a registry manifest digest: pin a verified registry digest
when one is available, rather than assuming a local tag is immutable. No
retagging or rebuild is needed if the approved image already has both tags.

The worker joins `control`, `inference` and the project-owned internal
`narration-ocr` network; Docling joins only `narration-ocr`, with no published
port, database/Redis credentials, Docker socket or mounts. The private DNS name
is `docling-ocr:9623`. Network attachment does not allocate a GPU. Docling
runs as UID 1001 with a read-only root, dropped capabilities, no-new-privileges,
0.5 CPU, 2 GiB memory, 64 PIDs and a 512 MiB restricted temporary filesystem.
The server's document and synchronous wait limits are 120 seconds; the worker's
HTTP read timeout is 120 seconds per read, not a proven total wall-clock limit.

RapidOCR models are bundled in the Docling image, not a Docker volume, and
offline mode prevents model downloads. The independent project-scoped
`oju-video-forge_narration-models` volume is mounted read-only by the worker
for approved Whisper assets. Do not create a replacement OCR model volume or
copy model assets from another project.

The separate GPU overlay is experimental and must be merged after the CPU
narration overlay in the same project. Do not enable it until its dependency
lock, resource limits and actual CUDA inference are verified; CPU is the
default and does not depend on GPU acceptance.

### Cutover and rollback

Keep the historical standalone Docling container and its image until the
canonical service has passed real private-network OCR and the owner authorizes
retirement. On a constrained host, arrange an owner-approved maintenance window:
stop the standalone container without removing it, start and validate the
canonical service, then retain the standalone container for rollback. Do not
run two resource-intensive Docling instances concurrently. If cutover fails,
stop the new service and restore the prior approved worker configuration and
standalone service; do not delete volumes or use `down -v`. Reconcile the
application state and obtain approval before restarting any shared service.

The worker image pins Python 3.12 Trixie by digest, eSpeak NG
`1.52.0+dfsg-5` (GPL-3.0-or-later; bundled English voices), and FFmpeg
`7:7.1.5-0+deb13u1`. OCR runs in the separate pinned Docling Serve CPU image;
Tesseract is not installed in the narration worker. Python ASR dependencies are
hash-locked in `requirements-narration.lock.txt`: faster-whisper `1.2.1` and
CTranslate2 `4.6.0`. Packages imported successfully in the built Linux
container. No Windows runtime dependencies were installed.

The narration image additionally pins Debian's zlib security backport
`1:1.3.dfsg+really1.3.1-1+deb13u1`. Its security gate remains blocked on other
unresolved Trixie vulnerabilities; see the
[2026-11-09 image remediation evidence](reviews/2026-11-09-NARRATION-IMAGE-SECURITY.md).
The successful runtime smoke is not a security acceptance or deployment approval.

### ASR model authorization gate

No ASR model was downloaded or inferred during this implementation.
Proposed source: `Systran/faster-whisper-tiny` (MIT), revision
`d90ca5fe260221311c53c58e660288d3deb8d356`, at
<https://huggingface.co/Systran/faster-whisper-tiny/tree/d90ca5fe260221311c53c58e660288d3deb8d356>.

| File | Bytes | Published identity |
| --- | ---: | --- |
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

OCR uses Docling Serve `1.37.0` and RapidOCR `3.10.0` with the pinned CPU image
`quay.io/docling-project/docling-serve-cpu@sha256:8376600bcc1ba41d06b26f4af10fe1ba0071578e85d0857447f4c2fdda9308a8`.
The worker posts only bounded JPEG frames to the fixed
`http://docling-ocr:9623/v1/convert/file` endpoint, with the `rapidocr` preset.
The response, extracted text and request time are bounded; redirects, proxy
environment variables and retries are disabled. The response records Docling,
RapidOCR, ONNX Runtime and model-file checksum provenance.

The OCR service has no published port, model or application-storage mounts, or
database/Redis credentials. It is connected only to the worker through the
internal `narration-ocr` network; its root filesystem is read-only and its
CPU, memory, process count and temporary storage are bounded. Docling Serve
1.37.0 does not provide a built-in OCR-preset allowlist: custom OCR configuration
is disabled, and only the trusted worker client is allowed onto this network;
the client explicitly sends `rapidocr`. This is a network/client boundary, not
a server-side preset whitelist.

At most 12 timestamped frames are sampled over a maximum 600-second source.
Sampled grayscale change detection identifies major visual changes, not exact
mouse actions or a complete scene-cut timeline. At most six frames may be sent
to a configured local Ollama vision model with explicit UI opt-in. Set
`NARRATION_VISION_URL` to an approved
loopback/`host.docker.internal`/`ollama` HTTP endpoint and
`NARRATION_VISION_MODEL` to an approved installed model. Public endpoints,
redirects and environment HTTP proxies are not used. Capability is checked before
inference; the model is requested with `num_gpu=0`, two threads, bounded output
and no keep-alive. No inference service is exposed publicly or reconfigured.
Interpretations are explicitly uncertain and require human review. Real VLM
interpretation was not exercised; installed-model metadata advertised vision.

OCR model files are bundled in the pinned Docling CPU image; the narration
worker cannot select or supply OCR models. Hugging Face offline mode and
Docling's remote-service option are disabled. The earlier synthetic OCR
acceptance used Tesseract before this replacement and is historical evidence;
it does not validate the Docling service or its RapidOCR inference.
The current service smoke is `scripts/smoke_docling_ocr.py`; unlike the
historical Tesseract runtime smoke, it requires the private Docling network but
does not require or download the Whisper model.

### Docling integration verification and image security (2026-11-11)

The local synthetic MP4 smoke passed through the narration frame extractor and
the healthy Docling sidecar. RapidOCR returned `SYNTHETIC-OCR-TEST` and
provenance for Docling Serve 1.37.0, RapidOCR 3.10.0, ONNX Runtime 1.31.0 and
the three bundled model checksums. No Whisper model or remote inference was
used. The merged Compose configuration passed; runtime inspection confirmed
UID 1001, read-only root, CPU/memory/PID limits, dropped capabilities, no
published ports or credential environment variables, and an internal-only OCR
network.

Trivy 0.76.2 scanned OS and library vulnerabilities with one refreshed database
(`UpdatedAt: 2026-11-11T19:04:54Z`), `--scanners vuln
--severity HIGH,CRITICAL`, and no `--ignore-unfixed`. These are strict findings,
not security acceptance:

| Image | Local image ID | OS | HIGH | CRITICAL | H/C records | Unique H/C CVEs | Packages |
|---|---|---|---:|---:|---:|---:|---:|
| Baseline `ovf-narration:test-security` | `sha256:99a0ab1af5e2b8785dfedb3916de0d209aafa86a3d307e10fc6af9bcd135d83b` | Debian 13.7 | 295 | 1 | 296 | 74 | 44 |
| Replacement `oju-video-forge-narration:docling-ocr` | `sha256:a6a709b69b2e381eee281a7a241150423ce53e3ed95527e6809d0b5ac880c7af` | Debian 13.7 | 281 | 1 | 282 | 66 | 41 |
| OCR `oju-video-forge-docling-ocr:1.37.0` | `sha256:61a0357faf78e0e8df2fa5e20058d03a398d0517e6b6e4ad3c0b67c3fead7988` | CentOS Stream 9 | 16 | 0 | 16 | 16 | 8 |

All three strict image scans exited 1. The eight baseline-only CVEs are the six
Tesseract CVEs and two libcurl CVEs; their `tesseract-ocr`, `libtesseract5` and
`libcurl4t64` packages are absent from the replacement image. This is package
removal, not patching or acceptance. No replacement-only H/C CVEs were reported.
The replacement still has 281 HIGH and one CRITICAL finding, dominated by the
FFmpeg/libav package family (17 repeated package findings per binary package).
The Docling findings are in `pyasn1` (4), `aiohttp` (3), Jackson Databind (3),
Jackson Core (2), and Guava, oauthlib, httpcore5 and virtualenv (one each).
Trivy reports fixed versions for those Docling findings, but this pinned base
image does not contain them; refreshing that base requires compatibility
validation and a new scan. No finding was suppressed and no risk was accepted.

Trivy's Dockerfile configuration scan reported zero HIGH/CRITICAL
misconfigurations for the narration and Docling Dockerfiles using embedded
checks. The Compose YAML was not recognized by that misconfiguration scanner;
its isolation was checked with the focused Compose tests, rendered
configuration and runtime inspection instead. The image scan JSON was retained
only in temporary local storage, not committed.

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
| --- | ---: | --- |
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
CI reports all HIGH/CRITICAL narration-image findings and fails when Trivy
identifies an available fix. Unfixed findings remain visible and are not
considered remediated by a passing gate.
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
