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
