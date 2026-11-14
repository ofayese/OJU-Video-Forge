# Silent-recording integration acceptance — 2026-11-13

## Disposition: PARTIAL

The local workflow passes deterministic and synthetic-media validation, and
source-frame timestamps now use decoded presentation times. Real model inference,
real training OCR/vision, and the authorized SharePoint pilot remain blocked.
This is not deployment or security acceptance.

## Model and resource discovery

The running OJU API has no `AGENTS_ENABLED`, `AGENT_PROVIDER`, `AGENT_URL`,
`AGENT_MODEL`, or `AGENT_MODEL_DIGEST` environment values. No OJU-owned Ollama
service or installed approved model was identified. A separately managed model
service was neither queried nor reused. No weights were downloaded.

The OJU host reports an NVIDIA RTX 5070 Ti with 16,303 MiB total VRAM and
approximately 13,694 MiB free at the last measurement. Available system memory
was 5.32 GiB initially and 4.51 GiB at the latest check. These are point-in-time
observations, not a reservation or proof that a model can safely load.

**Candidate for owner approval and isolated evaluation:** Ollama
`qwen3-vl:4b` as one text-and-vision model, avoiding two simultaneously resident
models. The official Ollama library currently lists a 3.3 GB package comprising
4.02B parameters, Q4_K_M weights (2.5 GB), and a BF16 vision projector (839 MB).
Ollama documents schema-constrained structured output for vision models. The
model's advertised long context is not a suitable default for this workload:
start acceptance at 4,096 tokens, one request at a time, and measure peak VRAM
and host RAM before considering any concurrency. Ollama does not publish an
exact runtime-memory requirement for this configuration; 16 GiB VRAM is not
proof of capacity. The candidate is not installed or approved, and its local
content digest is unknown. Do not pull it without separate authorization.

Sources: [Ollama qwen3-vl:4b model page](https://ollama.com/library/qwen3-vl:4b)
and [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs).
If text-only generation is selected instead, [`qwen3:8b`](https://ollama.com/library/qwen3:8b)
is an alternative text model, not a vision substitute. Current code has separate
text-agent and vision endpoint/model settings; the vision path does not bind to
an immutable model digest, so do not claim pinned vision provenance until that
gap is addressed.

## Docling/RapidOCR

The canonical Compose overlay statically specifies the OJU-owned
`oju-video-forge/docling-ocr:1.38.2` service, a private internal
`narration-ocr` network, offline mode, no public port, 0.5 CPU, 2 GiB memory,
64 PIDs, and a pinned upstream Docling CPU base digest. Merged Compose
configuration validates.

The canonical image tag is absent locally, and no OJU `docling-ocr` service or
`narration-ocr` network is running. Therefore bundled OCR asset checksums,
service health, DNS, HTTP behavior, actual recognized text, timeout handling,
and OCR provenance were not runtime-verified. No second OCR service was started
beside existing workloads. A real synthetic OCR run needs an isolated acceptance
host with the approved image and model assets; do not stop or reuse another
workload to make room.

## Source-frame timestamps

`app/narration.py` now seeks relative to the video stream's start time and records
the decoded frame PTS reported by FFmpeg, normalized to that start and rounded
half-up to milliseconds. It fails explicitly when decoded PTS is missing or out
of range. FFprobe/FFmpeg tests generated synthetic CFR, VFR, and nonzero-start
MP4s, then verified each selected time against the source's decoded PTS list.
Additional real-media tests verify a scene transition, duplicate-frame removal,
millisecond rounding, and rejection of corrupt media. Sampling remains sparse
(at most 12 frames) and can miss brief transitions; it is not complete visual
event detection. The authorized SharePoint recording was not present in the
workspace and was not accessed.

## Workflow, approvals, and outputs

Prior synthetic acceptance used the existing project-scoped API and review
workflow with a mocked agent, real FFprobe/FFmpeg and eSpeak. The source checksum
remained unchanged; the generated annotated derivative differed as expected.
This was not real model generation, OCR, vision, or a real recording pilot.

Existing API authorization/revision tests cover evidence and script approval,
stale approvals, project roles, and artifact access. Training exports include
procedure JSON, teaching-script JSON, evidence manifest, module JSON/Markdown,
chapter index, lab guide, learner handout, assessment, answer key, screenshots,
and the narrated MP4. H5P, Moodle, SCORM, and xAPI interoperability are not
implemented. No human-approved module was produced from a real recording.

Browser end-to-end testing was not run. The active API/frontend containers
started before this branch revision; updating or redeploying them was not
authorized. Frontend typecheck, unit tests, and build had previously passed on
the current checked-in frontend lockfile. No live UI or cross-project browser
acceptance is claimed.

## Current image security

Trivy 0.76.2 scanned the following exact local Docker image IDs with
`--scanners vuln --severity HIGH,CRITICAL --exit-code 1`, without ignoring
unfixed findings. The vulnerability DB was updated at
`2026-11-13 12:56:33 UTC`, next update `2026-11-14 12:56:33 UTC`, downloaded
`2026-11-13 15:39:44 UTC`. Java DB scanning was skipped; the vulnerability
scanner covers Debian OS packages and Python packages. Trivy warned that it
selected some severities from other vendors. No new CycloneDX or SPDX SBOM was
generated for this follow-up.

| Image | Local image ID (not registry manifest digest) | HIGH | CRITICAL | Unique H/C CVEs | Entries with a fix |
| --- | --- | ---: | ---: | ---: | ---: |
| Synthetic acceptance `cpu-candidate` | `sha256:445a991c743bdfc3737b09ad7b8a5f559bc1cc222ac571edcd6e7ac44211f794` | 281 | 1 | 66 | 0 |
| `docling-ocr` narration candidate | `sha256:a6a709b69b2e381eee281a7a241150423ce53e3ed95527e6809d0b5ac880c7af` | 281 | 1 | 66 | 0 |
| Previous `security-dc8fa7a` image | `sha256:48dfb2630759dcb56546dceb570b5e02baaf39220e1bdd6b152b35da0cebb7a6` | 295 | 1 | 74 | 0 |
| Active OJU worker image | `sha256:51ce99d59fbc0fa55e24eac8bde3ce3c385b3d8a0c2042da49f2dfc67d7e2199` | 295 | 1 | 74 | 0 |

Relative to the older image, the candidate has 14 fewer HIGH package records and
8 fewer unique CVEs, with no new unique H/C CVE IDs. This is package inventory
reduction, not proof that retained software was patched. No finding in these
reports has a Trivy `FixedVersion`; this does not prove that no upstream fix
exists, only that this database/package match provides no fixed version.

Priority 1 is CRITICAL `CVE-2026-6653` in Debian `libxml2`
`2.12.7+dfsg+really2.9.14-2.1+deb13u3`; Trivy lists no fixed version. `ldd`
confirms the installed `/usr/bin/ffmpeg` links to `libxml2.so.2`. The package is
an OS runtime component, not a direct Python requirement. Linkage is evidence of
presence, not proof that the CVE-specific vulnerable path is reachable; no
non-reachability or residual-risk approval is claimed.

Priority 2 is the 17 HIGH CVEs reported against FFmpeg and each of its linked
`libav*` package records. Priority 3 is the 14 HIGH findings per Kerberos
runtime package family; then the remaining `libxml2`, `libcjson`, and
`util-linux` records. All remain without scanner-listed fixed versions.
The installed FFmpeg is the Debian package `7:7.1.5-0+deb13u1`, installed via
APT in the image build; it is **not source-built FFmpeg**. It remains in the
media runtime, and no functionality was removed to reduce counts.

## Validation evidence and blockers

Linux Docker on the Windows development host:

- Full backend: `python -m pytest -q -p no:cacheprovider` — **323 passed,
  16 skipped, 5 warnings**.
- Focused timestamp/media regression — **15 passed**, including actual
  CFR/VFR/offset, transition, duplicate, corrupt-input, and rounding cases.
- Ruff — passed; repository validation, OpenAPI export, and `pip check` — passed.
- Canonical Compose `config --quiet` — passed without starting services.
- Synthetic API-to-render smoke — passed using a mock agent and actual
  eSpeak/FFmpeg. It is not model/OCR acceptance.
- Frontend: 65 tests, typecheck, and production build had passed previously;
  frontend sources were unchanged by this follow-up.
- Exact-commit GitHub CI was not run; changes were not pushed.

The standard backend run skips 16 service-dependent tests. A previous isolated
PostgreSQL/Redis dispatch run passed 12 tests, but no new queued training-flow
acceptance was run against those services after this follow-up.

**Required before PASS:** approve and install a local model (or approve a
separately provisioned endpoint), pin and verify its exact digest/capabilities,
run real structured generation and review; provision an isolated canonical
Docling image/network and verify RapidOCR output/provenance; provide an
authorized recording copy and complete the full human-reviewed pipeline;
perform browser E2E against the branch without deploying over active services;
remediate or formally adjudicate the image findings, then rerun current scans and
CI. No model download, shared-service shutdown, deployment, push, merge, or
security-risk acceptance was performed.

## Exact validation commands

The backend, Ruff, metadata, OpenAPI, Compose, and smoke commands below ran in
Linux Docker containers on the Windows host. Source mounts were read-only and
the test containers had no external network. The strict Trivy scans each exited
1 because HIGH/CRITICAL findings remain.

```powershell
docker run --rm --network none --memory 512m --cpus 0.5 --pids-limit 64 --mount "type=bind,source=${PWD}\app,target=/opt/forge/app,readonly" --mount "type=bind,source=${PWD}\tests,target=/opt/forge/tests,readonly" oju-video-forge-tests:training-local python -m pytest -q -p no:cacheprovider

docker run --rm --network none --memory 512m --cpus 0.5 --pids-limit 64 --mount "type=bind,source=${PWD}\app,target=/opt/forge/app,readonly" --mount "type=bind,source=${PWD}\tests,target=/opt/forge/tests,readonly" oju-video-forge-tests:training-local python -m pytest -q -p no:cacheprovider tests/test_narration.py -k 'scenes or probe_rejects_corrupt'

docker run --rm --network none --memory 256m --cpus 0.25 --pids-limit 32 --mount "type=bind,source=${PWD},target=/workspace,readonly" -w /workspace oju-video-forge-tests:training-local ruff check --no-cache app tests alembic scripts

docker run --rm --network none --memory 256m --cpus 0.25 --pids-limit 32 --mount "type=bind,source=${PWD},target=/workspace,readonly" -w /workspace oju-video-forge-tests:training-local sh -c 'python scripts/validate_repository.py && python scripts/export_openapi.py --output /tmp/training-openapi.json && python -m pip check'

docker compose --env-file .env.development.example -f compose.yaml -f compose.narration.yaml config --quiet

docker run --rm --network none --memory 768m --cpus 0.5 --pids-limit 64 --mount "type=bind,source=${PWD}\app,target=/opt/forge/app,readonly" --mount "type=bind,source=${PWD}\scripts,target=/opt/forge/scripts,readonly" -w /opt/forge --entrypoint python oju-video-forge-narration:cpu-candidate -m scripts.synthetic_training_smoke

trivy image --skip-db-update --skip-java-db-update --offline-scan --scanners vuln --severity HIGH,CRITICAL --exit-code 1 --format json --output 'C:\Users\akoso\.copilot\session-state\30efeaef-b9ef-45a4-a28f-e58b7eb712df\files\trivy-cpu-candidate-2026-11-13.json' oju-video-forge-narration:cpu-candidate

trivy image --skip-db-update --skip-java-db-update --offline-scan --scanners vuln --severity HIGH,CRITICAL --exit-code 1 --format json --output 'C:\Users\akoso\.copilot\session-state\30efeaef-b9ef-45a4-a28f-e58b7eb712df\files\trivy-docling-ocr-2026-11-13.json' oju-video-forge-narration:docling-ocr

trivy image --skip-db-update --skip-java-db-update --offline-scan --scanners vuln --severity HIGH,CRITICAL --exit-code 1 --format json --output 'C:\Users\akoso\.copilot\session-state\30efeaef-b9ef-45a4-a28f-e58b7eb712df\files\trivy-security-baseline-2026-11-13.json' oju-video-forge-narration:security-dc8fa7a

trivy image --skip-db-update --skip-java-db-update --offline-scan --scanners vuln --severity HIGH,CRITICAL --exit-code 1 --format json --output 'C:\Users\akoso\.copilot\session-state\30efeaef-b9ef-45a4-a28f-e58b7eb712df\files\trivy-running-worker-2026-11-13.json' sha256:51ce99d59fbc0fa55e24eac8bde3ce3c385b3d8a0c2042da49f2dfc67d7e2199
```

Frontend sources were unchanged by this follow-up. The previously passing
in-container command was `npm run typecheck && npm test && npm run build`
(**65 tests passed**); no browser E2E command was run.

## Latest acceptance check at HEAD `d47256e0a68388c9b2806c85ac4be99e92b77835`

The branch is `feat/silent-recording-training`, two commits ahead of
`origin/main`. The timestamp correction and synthetic tests remain present.
The only pre-existing worktree change is the unstaged table-header formatting
edit in this document's companion workflow guide; it was preserved. No other
files were created in the repository by this check.

**Inference gate — BLOCKED.** The active OJU API has no agent-provider settings.
The OJU render worker has `NARRATION_VISION_URL` and `NARRATION_VISION_MODEL`
variables defined but empty; no OJU Ollama service or model is configured. No
endpoint was contacted. The official Ollama library describes `qwen3-vl:4b` as
a vision-language model requiring Ollama 0.12.7 or later. Its listed package is
3.3 GB: 2.5 GB Q4_K_M model weights and an 839 MB BF16 vision projector
(415M parameters). Ollama documents schema-constrained structured output for
vision models. These are candidate specifications, not a local inference result
or a memory guarantee. At measurement, Windows reported 5.37 GiB free physical
RAM and the RTX 5070 Ti reported 14,006 MiB free VRAM. Runtime memory and context
overhead are unmeasured. No weights were downloaded and no exact local model
digest exists.

**Owner action for inference:** authorize the official `qwen3-vl:4b` model
download and provision it in an OJU-owned Ollama service on a private internal
network reachable only by the OJU render worker, with no published port and no
NuSass connection. Reserve at least the listed 3.3 GB for model files plus
runtime/cache overhead; acceptance must measure peak RAM/VRAM at 4,096-token
context and concurrency one. After installation, record Ollama's full local
model digest and configure `AGENTS_ENABLED=true`, `AGENT_PROVIDER=ollama`,
`AGENT_URL`, `AGENT_MODEL=qwen3-vl:4b`, and `AGENT_MODEL_DIGEST` to that verified
64-character lowercase SHA-256. Also configure `NARRATION_VISION_URL` and
`NARRATION_VISION_MODEL=qwen3-vl:4b`. The current vision adapter does not
independently bind its model to an immutable digest; approve and verify that
provenance change or use a server-side immutable model binding before claiming
pinned vision acceptance.
The owner's test host must also have enough measured headroom to build the OCR
image and run its 2 GiB-capped container without consuming the existing free-
RAM reserve.

**Docling gate — BLOCKED at runtime.** The canonical merged Compose config
resolves `oju-video-forge/docling-ocr:1.38.2` on internal `narration-ocr` only,
with no ports or volumes, read-only root, 0.5 CPU, 2 GiB memory, and 64 PIDs.
It has no DB, Redis, or Docker-socket mount. The pinned upstream base image
`quay.io/docling-project/docling-serve-cpu@sha256:7a480a1604fa3b42efeddc7db9527690d9f9bc903226136013e2bfe6b1a4dfb0`
is cached, but the canonical application image and service are absent. The
Dockerfile does not copy an OCR model asset; offline RapidOCR asset availability
cannot be inferred from `HF_HUB_OFFLINE=1`. No image was built, service started,
or OCR request sent. The separate `services/docling-ocr/docker-compose.yml`
still names the noncanonical `oju-video-forge-docling-ocr:1.38.2` image and
`ocr-network`; it was not used. No NuSass service API was contacted, and no
NuSass assets were modified or reused.

**Recording and UI gates — BLOCKED.** `test-library-creation.mp4` was not found
in the repository, Downloads, Desktop, or Documents. SharePoint was not
accessed; there is no source hash, OCR text, timestamped observed step, or
generated pilot artifact. Read-only browser inspection reached the OJU sign-in
page with no console errors. No test credentials or project were available, so
no account was created and no upload, analysis, approval, render, or download
action was submitted. No human evidence or script approval occurred.

**Current vulnerability check — FAIL.** Trivy 0.76.2 rescanned the exact local
Docker image IDs below with the cached database updated
`2026-11-13 12:56:33 UTC`, without suppressions. Both are narration images; the
second tag is not the canonical Docling OCR service image.

| Scan input | Docker image ID | HIGH | CRITICAL | Unique H/C CVEs | Entries with fixed versions |
| --- | --- | ---: | ---: | ---: | ---: |
| `oju-video-forge-narration:cpu-candidate` | `sha256:445a991c743bdfc3737b09ad7b8a5f559bc1cc222ac571edcd6e7ac44211f794` | 281 | 1 | 66 | 0 |
| `oju-video-forge-narration:docling-ocr` | `sha256:a6a709b69b2e381eee281a7a241150423ce53e3ed95527e6809d0b5ac880c7af` | 281 | 1 | 66 | 0 |

CRITICAL `CVE-2026-6653` remains on Debian `libxml2`
`2.12.7+dfsg+really2.9.14-2.1+deb13u3`, with no Trivy `FixedVersion`.
Bounded, offline `dpkg-query` and `ldd` verification confirmed Debian APT
FFmpeg `7:7.1.5-0+deb13u1` links to `libxml2.so.2`; this proves presence, not
CVE-specific reachability. The principal HIGH groups are FFmpeg/libav (17 CVEs
each across package records) and Kerberos libraries (14 each); none has a
scanner-listed fixed version. The scanner commands exited 1 as required.

Exact additional checks:

```powershell
docker compose --env-file .env.development.example -f compose.yaml -f compose.narration.yaml config --format json

docker run --rm --network none --read-only --memory 256m --cpus 0.25 --pids-limit 16 --entrypoint /bin/sh oju-video-forge-narration:cpu-candidate -c 'dpkg-query -W ffmpeg libxml2'

docker run --rm --network none --read-only --memory 256m --cpus 0.25 --pids-limit 16 --entrypoint /bin/sh oju-video-forge-narration:cpu-candidate -c 'ldd /usr/bin/ffmpeg | grep -E "libxml2|libav"'

trivy image --skip-db-update --skip-java-db-update --offline-scan --scanners vuln --severity HIGH,CRITICAL --exit-code 1 --format json --output 'C:\Users\akoso\.copilot\session-state\30efeaef-b9ef-45a4-a28f-e58b7eb712df\files\trivy-current-cpu-candidate.json' sha256:445a991c743bdfc3737b09ad7b8a5f559bc1cc222ac571edcd6e7ac44211f794

trivy image --skip-db-update --skip-java-db-update --offline-scan --scanners vuln --severity HIGH,CRITICAL --exit-code 1 --format json --output 'C:\Users\akoso\.copilot\session-state\30efeaef-b9ef-45a4-a28f-e58b7eb712df\files\trivy-current-narration-docling-labeled-candidate.json' sha256:a6a709b69b2e381eee281a7a241150423ce53e3ed95527e6809d0b5ac880c7af
```

**Disposition remains PARTIAL.** To proceed, the owner must approve and
provision the isolated local model; provide an authorized recording copy and
disposable OJU test account/project; and perform the evidence and script human
approvals. On a host with sufficient measured headroom, build the OJU OCR image
from its pinned base and locked dependencies, verify OCR assets are already
present with remote model downloads disabled, run bounded OCR, and complete the
project-scoped browser workflow. Security findings still need upstream
remediation or explicit owner adjudication; no risk acceptance is implied.

## Follow-up — acceptance model wiring and resource gate (2026-11-13)

The owner has since authorized downloading the official `qwen3-vl:4b` model and
configuring a dedicated OJU acceptance Ollama runtime. This authorization did not
authorize use of NuSass assets or the import of a recording from an alternative
source. The exact SharePoint link for `test-library-creation.mp4` is still
pending.

**Code/configuration — PASS, runtime — BLOCKED.** The current branch remains
`feat/silent-recording-training` at `d47256e0a68388c9b2806c85ac4be99e92b77835`;
no commit, push, merge, or deployment was made. Vision now requires a configured
64-character lowercase model digest, verifies `/api/tags` and vision capability
before and after generation, and records the model digest plus model-info hash.
Agent and vision requests no longer force CPU inference. The `vision_configured`
capability is false unless endpoint, model, and digest are all present.

The acceptance-only `compose.ollama.yaml` pins official Ollama
`0.40.16` Linux/amd64 to
`sha256:a372a1c0d1e15b68e9521189785c3ca17795c26c2356f8b1b10122cfb59ed0d7`.
That image was not pulled, so this is a configured immutable reference, not a
verified local image identity. The logical `ollama-models` named volume is
acceptance-project-scoped. Ollama is capped at 4 CPUs, 2 GiB and 128 PIDs, is
configured to request the GPU, and is attached only to internal
`agent-inference` after bootstrap. The temporary, acceptance-only bootstrap
overlay adds a non-internal network solely for the approved model pull and must
be removed by Compose reconciliation immediately afterward. Neither model
weights nor a model digest were obtained; no OJU model generation or vision
inference ran.

The canonical acceptance Docling definition remains
`oju-video-forge/docling-ocr:1.38.2` on internal `narration-ocr`, with no
published port or volume, read-only root, CPU/memory/PID bounds, and no DB,
Redis, or Docker-socket attachment. No local `1.38.2` image was built or found
in the local image-list inspection; the older `1.37.0` tags were not used. Due
to the host-memory gate, offline RapidOCR asset presence was not inspected,
the image was not built or started, and no OCR request was sent. The legacy
`services/docling-ocr/docker-compose.yml` drift remains out of the acceptance
path. No NuSass API was contacted, and no NuSass model, volume, media, or
credential was read, mounted, stopped, or modified. Static configuration is not
runtime isolation evidence.

**Focused regression — PASS.** In a network-isolated Docker test container with
512 MiB RAM, 1 CPU, 128 PIDs and read-only mounts of the current application and
two test modules, `tests/test_agents.py` and `tests/test_narration.py` completed
with **75 passed, 1 Starlette/httpx deprecation warning** in 5.36 seconds. The
lowest measured free host RAM during this check was 4.85 GiB. The tests include
vision model identity match/mismatch and identity-change cases, GPU-default
request options, and the existing synthetic frame-timestamp regressions; they
do not establish real model, OCR, or media acceptance.

**Compose/helper checks — PASS, no services started.** Both merged acceptance
configurations passed `docker compose ... config --quiet`: base + acceptance +
narration + Ollama, with and without `compose.ollama-bootstrap.yaml`. The
configuration has no host-published Ollama or Docling port; the OCR service is
on `narration-ocr`, while API and worker join `agent-inference`. Bash helper
syntax and both regular/bootstrap Compose configurations passed. The
PowerShell helper plan selected the five expected files for acceptance and
rejected Ollama selection for the application environment. Python AST parsing
for the five changed source/test files and `git diff --check` passed.

**Resource/runtime gate — BLOCKED.** Host free physical memory was 4.09 GiB at
the last measurement, only 0.09 GiB above the required 4 GiB floor. No Ollama
image/model pull, Docling build, acceptance stack startup, inference, browser
workflow, Trivy refresh, or SBOM generation was attempted. The repository
validator container was deferred when its preflight measured less than its
4.5 GiB launch margin. The previous Trivy results above are historical for this
follow-up; there is no fresh scan of the configured Ollama reference or a
Docling `1.38.2` image.

**Recording, review and browser gates — BLOCKED.** The recording link has not
been supplied, so no download, media hash, source-frame extraction, OCR,
procedure, lesson, narration, render, or training-package artifact was created.
No evidence or script was approved on a human's behalf. No acceptance service
was started and no browser E2E was run.

**Current disposition: PARTIAL.** The deterministic code/configuration increment
and focused regressions pass. Real-model, real-OCR, recording, human-review,
narrated-render, browser, and current-security acceptance remain unverified.
Resume runtime work only when measured host RAM exceeds the applicable service
peak by at least 4 GiB (at least 6 GiB free before a 2 GiB-capped service, with
additional headroom for an uncapped image build), and after the owner provides
the exact authorized SharePoint recording link. Preserve the active NuSass
workloads and keep PR/deployment state unchanged.

## Follow-up — Ollama moved into the canonical main project (2026-11-13)

**Model volume transfer — PASS.** The owner-authorized model files were copied
from the stopped acceptance volume into a Compose-created
`oju-video-forge_ollama-models` volume. Before source cleanup, all 10 files
(3,336,630,473 bytes) matched by relative path, byte length, and SHA-256; the
canonical inventory hash was
`f408aed10edd8d25518cc58da55e4aac7c5b5c348ed36389af3745159ef998fe`.
The destination volume carries Compose labels for project `oju-video-forge`
and logical volume `ollama-models`. After verification, the stopped
`oju-video-forge-acceptance-ollama-1` container and its
`oju-video-forge-acceptance_ollama-models` volume were removed. The only
remaining model volume from this Ollama overlay is main-project-owned.

**Canonical service ownership and isolation — PASS.** The exact merged
configuration is `oju-video-forge` + `compose.yaml` +
`compose.narration.yaml` + `compose.ollama.yaml`. The service is
`oju-video-forge-ollama-1`, with project/service labels `oju-video-forge` /
`ollama`, image
`ollama/ollama:0.40.16@sha256:a372a1c0d1e15b68e9521189785c3ca17795c26c2356f8b1b10122cfb59ed0d7`,
and model volume `oju-video-forge_ollama-models`. It is healthy on only the
internal `oju-video-forge_agent-inference` network, has no published host port,
no extra mounts, no Docker socket, and no DB/Redis connection. Its runtime has
a read-only root, all capabilities dropped, GPU access, 4 CPU / 2 GiB / 128 PID
limits, and `OLLAMA_NO_CLOUD=1`. The private-network `/api/tags` request
returned `qwen3-vl:4b` with full model digest
`a1b29c246319cdcb1f3842f36d29f198f737c0cf516ba1bcba34b1b974e87980`,
size 3,336,619,180 bytes, Q4_K_M, 4.0B parameters. This was metadata inspection,
not inference. `/api/show` reports the `parent_model` string
`dhiltgen/qwen3-vl:4b-thinking-q4_K_M-llamacpp`; the tag was pulled from the
official Ollama library, but that declared lineage has not been independently
verified and must be reconciled before relying on model output.

The acceptance Ollama instance was already stopped before this change. It and
its model volume were removed only after the main-project copy and digest were
verified. The separately named NuSass Ollama container was already exited and
was not changed. The existing main API and render worker remained healthy and
running; neither was restarted. Their current network lists do not yet include
`oju-video-forge_agent-inference` (API: `backend`, `control`; render worker:
`control`, `inference`), so application-to-Ollama connectivity is not yet
available. Applying the new network membership requires an owner-approved,
scoped reconciliation of those two existing containers. No inference request
was made and `ollama ps` showed no loaded model.

**Resource observations.** Immediately before main-service startup, Windows
reported 8.21 GiB available host RAM and the RTX 5070 Ti reported 13,782 MiB
free VRAM (16,303 MiB total). Available host RAM stayed at or above 7.67 GiB
during volume copying and Ollama startup, and was 7.92 GiB after startup. These
are point-in-time host measurements; Docker container memory limits are not
proof that a later inference run has adequate headroom.

**Exact checks.**

- `.\scripts\forge.ps1 -Environment application -Narration -Ollama config --format json` — passed; resolved project, internal network, no `ports` property, and the main-project model volume.
- `.\scripts\forge.ps1 -Environment application -Narration -Ollama up --detach --no-deps --wait ollama` — passed; only Ollama was selected, health became `healthy`, minimum observed host RAM during startup was 7.67 GiB.
- `docker inspect oju-video-forge-ollama-1` and `docker port oju-video-forge-ollama-1` — passed; main-project labels and limits verified, no host-published port.
- `docker exec oju-video-forge-ollama-1 ollama ps` — passed; no model loaded.
- A 128 MiB / 0.25 CPU / 16-PID, network-attached temporary container requested `/api/tags` over the internal network — passed; exact model digest matched the copied model identity. No generation endpoint was called.
- Source/destination volume comparison in a network-disabled, 512 MiB / 0.5 CPU / 32-PID container — passed; all 10 file hashes matched.
- In `oju-video-forge-tests:training-local` with `--network none`, 768 MiB, 0.5 CPU and 64 PIDs: `python -m pytest -q -p no:cacheprovider tests/test_agents.py tests/test_narration.py` — 75 passed, 1 Starlette/httpx deprecation warning; `ruff check --no-cache app/agents.py app/narration.py app/narration_jobs.py tests/test_agents.py tests/test_narration.py` — passed. The combined command stopped with exit 127 at `bash -n scripts/forge.sh` because the image has no Bash; Bash validation was run separately under WSL.
- `docker run --rm --network none --memory 256m --cpus 0.25 --pids-limit 32 --mount "type=bind,source=${PWD},target=/workspace,readonly" -w /workspace oju-video-forge-tests:training-local python scripts/validate_repository.py` — passed.
- `bash -n scripts/forge.sh` and `bash scripts/forge.sh acceptance --narration --ollama config` — syntax passed; the helper rejected the acceptance Ollama selection as intended. PowerShell helper plan/rejection checks and `git diff --check` also passed.

**Remaining gates — PARTIAL/BLOCKED.** The Ollama acquisition, main-project
ownership, and service startup gates pass. The independent model-runtime gate
remains blocked pending model-lineage verification, approved API/worker network
reconciliation, and structured generation/visual inference. Docling build/OCR,
the authorized recording import, human evidence/script approval, narrated
rendering, browser E2E, current Trivy scan/SBOM, and CI remain unverified. Do
not enable agent/vision settings or claim overall acceptance until those gates
are completed.

## Follow-up — bounded Ollama runtime probes

The model was exercised only through synthetic requests on the private
`oju-video-forge_agent-inference` network. This supersedes the earlier statement
that no inference request had been made; it does not establish application
connectivity because the running API and worker have not been attached to that
network.

**Synthetic vision probe — PASS, limited.** A synthetic image with a visible
`SAVE` button produced structured visual output identifying the text and button.
The request took 52.1 seconds; minimum observed host RAM was 7.90 GiB and minimum
free GPU memory was 9,141 MiB. This is not OCR acceptance, a real-recording
result, or evidence that API/worker callers can reach Ollama.

**Training adapter generation — BLOCKED.** The exact `generate_training`
adapter request using a synthetic, already-reviewed one-frame procedure
returned `agent_generation_invalid_json`. A bounded diagnostic using the same
model, prompt, schema and options showed `done=true`, `done_reason=length`,
`eval_count=3072`, `prompt_eval_count=234`, an empty `response`, and 14,901
characters in the separate `thinking` field after 22.73 seconds. The adapter
does not accept thinking text as draft JSON. A small, unrelated JSON-schema
probe did return `{"ok": true}` in 59 generated tokens, but also returned a
non-empty `thinking` field despite `think=false`; this does not validate
`TrainingDraft` or script-evidence validation.

The local API reports Ollama `0.40.16`. `/api/show` reports the expected model
digest and completion, vision, tools, and thinking capabilities, while retaining
the unresolved parent-model string
`dhiltgen/qwen3-vl:4b-thinking-q4_K_M-llamacpp`. Do not treat the
`think=false` request field as proof that reasoning was disabled. The model
lineage and generation behavior remain acceptance blockers; do not increase
token limits or weaken structured-output/evidence validation without new
evidence.

**Resource observations during bounded diagnostics.** Minimum host available
RAM was 9.01 GiB and minimum GPU free memory was 9,259 MiB during the full
generation diagnostic. Afterward, Windows reported 9.84 GiB available host RAM
and 13,703 MiB free GPU memory. The operations retained the existing 4 GiB
host-RAM reserve and did not restart or reconfigure application or NuSass
services.

**Exact checks.**

- In `oju-video-forge-tests:training-local`, network-disabled, 768 MiB / 0.5 CPU / 64 PIDs: `python -m pytest -q -p no:cacheprovider tests/test_agents.py tests/test_narration.py` — 75 passed, 1 existing Starlette/httpx deprecation warning.
- In the same container: `ruff check --no-cache app/agents.py app/narration.py app/narration_jobs.py tests/test_agents.py tests/test_narration.py` — passed; `python scripts/validate_repository.py` — passed.
- Synthetic `generate_training` adapter request through `oju-video-forge_agent-inference` — failed as described above; no draft was accepted or persisted.
- Exact schema diagnostic through `oju-video-forge_agent-inference` — passed for the minimal `ok` schema only; reasoning was still present.
- Model metadata request through the private network — returned API version `0.40.16`, the expected model digest, and the reported parent-model lineage.
- `git diff --check` — passed before this report update.

**Remaining gates — PARTIAL/BLOCKED.** Ollama main-project ownership and
synthetic vision pass. Structured training generation, model-lineage
reconciliation, API/worker connectivity, canonical Docling 1.38.2 OCR,
authorized recording import, human approvals, narration/render/export, browser
E2E, fresh Trivy/SBOM, and CI remain incomplete. The exact authorized recording
URL is still required. Keep agent/vision settings disabled and do not claim
overall silent-recording acceptance.
