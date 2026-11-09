# Audio workflow contract v1

The local API accepts version 1 workflows under `/v1/projects/{project_id}`. A
project is owned by the authenticated actor; project, audio, workflow, and artifact
reads are checked server-side against that owner. Uploads and outputs are stored below
the configured private `ARTIFACT_ROOT` using server-generated path keys. Client
filenames and paths are never used as storage paths.
Compose mounts the same host `${FORGE_DATA}/artifacts` directory into both API and
worker at `ARTIFACT_ROOT` (default `/artifacts`); both services use that
container-local path.
On POSIX, the storage root and generated directories are restricted to mode `0700`
and files to `0600`; Windows deployments must enforce equivalent ACLs for both
service identities.

## Audio assets

`POST /v1/projects` creates a project. `POST /v1/projects/{project_id}/audio`
accepts one bounded PCM WAV upload (64 MiB maximum, mono/stereo, 8–96 kHz, duration
up to two hours). The backend validates the audio container and metadata, assigns an
opaque asset ID, and persists the project ID, duration, size, media type, and SHA-256
before returning metadata. `GET /v1/projects/{project_id}/audio/{asset_id}` streams
the asset only to its project owner.

MP3, compressed WAV, and other audio formats are intentionally unsupported by this
local contract. No speech model or network service is required to upload audio or
export captions.

## Asynchronous workflow request

`POST /v1/projects/{project_id}/workflows` accepts:

```json
{
  "workflow_version": 1,
  "step": "captions",
  "audio_asset_id": "optional-project-audio-id",
  "captions": [
    {"start_ms": 0, "end_ms": 1000, "text": "Example caption"}
  ],
  "formats": ["srt", "vtt"]
}
```

`step` is one of `narration`, `transcription`, or `captions`. Narration requires
`text` and optional BCP-47-like `language` (for example `en` or `en-US`).
Transcription requires a project audio asset. Caption export requires `captions`
and may reference audio to enforce that cue ends fit its validated duration.
Unknown fields and workflow versions are rejected.

Caption timing uses integer milliseconds, half-open `[start_ms, end_ms)` intervals,
with `0 <= start_ms < end_ms <= 7,200,000`. Cues are ordered and non-overlapping;
there are at most 1,000 cues, each with 1–500 printable single-line Unicode
characters. Caption JSON is limited to 256 KiB. SRT uses sequential cue numbers and
comma milliseconds; WebVTT begins with `WEBVTT` and uses dot milliseconds. Both are
UTF-8 and are serialized deterministically.

## Job and artifact behavior

Workflow jobs are persisted before RQ dispatch. They expose `queued`,
`dispatch_pending`, `running`, `failed`, and `awaiting_review` status through
`GET /v1/projects/{project_id}/workflows/{job_id}`. Failed workflows may be retried
as a new linked job at most twice; duplicate retry creation is rejected. Duplicate
worker deliveries do not restart jobs outside queued/dispatch-pending states.

Caption export produces requested `.srt`/`.vtt` artifacts and a JSON manifest.
Manifest records identify the project and job, hash audio/caption inputs, and list
artifact IDs, sizes, and SHA-256 checksums. Retrieve artifacts with
`GET /v1/projects/{project_id}/artifacts/{artifact_id}`; the endpoint checks project
ownership, artifact ownership, and that the stored file remains within private
storage. It records an audit event and never accepts a filesystem path.

Narration and speech-to-text model adapters are not installed or configured in the
local core. Those jobs are persisted and fail with `provider_unavailable;
NOT_VERIFIED` until a reviewed local provider is available. Model installation,
licensing, execution, output validation, and GPU behavior are therefore **NOT
VERIFIED**. No external model service is contacted. The existing render operations
and timeline v1 remain unchanged when these optional steps are not used.

## Narration of an uploaded video

`narrate_existing_video` is distinct from `wan22_t2v` and
`render_timeline_v1`. The project-scoped `/v1/projects/{project_id}/narrations`
API implements analysis, script revisions, explicit approval, local synthesis,
and source-preserving derived MP4 rendering. It uses the existing jobs/outbox,
CPU queue, project roles, and private artifacts. See the
[narration workflow contract](narration-workflow-v1.md) for deployment,
provider gates, API calls and actual acceptance evidence.

Direct narration submission to `/v1/jobs` returns HTTP 422 with guidance to use
the approval workflow; it cannot bypass script review. Wan requests reject
`media_id`; there is no narration-to-Wan fallback.

The existing `audio_workflow` narration step is an unavailable text-to-speech
request, not narration of an uploaded video. Deterministic caption export is
implemented; transcription and real speech generation remain unavailable.
This legacy text-only workflow is not the new source-video narration API.
Automatic speech recognition still requires an owner-approved local ASR model;
no download occurs during a request. Unavailable ASR/VLM capabilities are reported
explicitly and manual script authoring remains separately selectable.

## Migration and validation

Apply Alembic revision `0004` after `0003` on a fresh isolated database. Unit/API
tests use synthetic PCM WAV and subtitle fixtures; they do not require PostgreSQL,
Redis, FFmpeg, GPU, or model downloads. A successful local test does not verify a
real speech model or production deployment.
