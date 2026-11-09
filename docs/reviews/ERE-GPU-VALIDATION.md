# ERE GPU Validation

**Status:** PASS — Wan 2.2 TI2V-5B generated and retrieved through the authenticated Video Forge API.
**Observed:** 2026-11-08

The original observations below are retained as historical snapshots. The Phase 4C
addendum at the end records the current result.

## Historical hardware observation

The local host's GPU configuration was not validated for this application. Detailed machine inventory is intentionally omitted from this public report.

## Inference services

- A separate local inference service was observed, but its model inventory is not included here and does not establish video-generation support.
- ComfyUI was unavailable during the historical local check.
- No ComfyUI version, Wan 2.2 TI2V-5B model, verified workflow, VRAM profile, or generated output was established.
- A new `app/comfyui.py` adapter was added, then only syntax-checked with `python -m py_compile app/comfyui.py` (exit 0). Its follow-up patch and tests were not completed.

## Validation results

| Check | Result |
|---|---|
| GPU identity and memory query | Observed |
| CUDA toolkit compatibility | Not verified |
| ComfyUI availability/version | Blocked / not available on observed local endpoint |
| Adapter unit tests and worker integration | Not completed |
| Wan model/workflow | Not verified |
| GPU admission/concurrency | Not verified |
| Generated output retrieval/probe/checksum/registration | Not run |

Do not terminate existing inference workloads or download/replace model artifacts without an authorized implementation step. No GPU-generation success is claimed.

## Historical readiness assessment (superseded by Phase 4C)

**Observed:** 2026-11-08. **Status: BLOCKED before model download or inference.**

- `docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi`
  exited 0 and enumerated the GeForce RTX 5070 Ti (compute capability 12.0,
  16,303 MiB total, 2,355 MiB in use at observation). Docker Desktop exposes an
  NVIDIA runtime. This verifies device enumeration only, not PyTorch kernels.
- The configured ComfyUI base is `pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime`.
  PyTorch's release notes identify 2.7 as the release adding Blackwell support
  and CUDA 12.8 wheels. NVIDIA lists the RTX 5070 Ti at compute capability 12.0.
  The current ComfyUI runtime therefore predates the documented Blackwell support.
- No local ComfyUI image or `oju-video-forge_models` volume exists. No approved
  Wan weights or `config/workflows/wan22-t2v-api.json` workflow are present.
  The configured workflow/node mapping is therefore unavailable.
- A disposable CUDA 12.8 container reported 877 GB free in its 1 TB filesystem;
  this is not evidence that model assets are installed. GPU VRAM had about 13.9
  GiB unoccupied at observation, before ComfyUI/model allocations.

Do not build the incompatible ComfyUI image, download model weights, or submit
inference until a reviewed ComfyUI/PyTorch image based on a Blackwell-supported
runtime and a pinned, approved Wan workflow/model package are available. No
driver, canonical application service, historical service, or volume was changed.

References:

- [PyTorch 2.7 release: Blackwell and CUDA 12.8 support](https://pytorch.org/blog/pytorch-2-7/)
- [NVIDIA CUDA GPU compute capability table](https://developer.nvidia.com/cuda/gpus)
- [NVIDIA CUDA 12.8 Blackwell support](https://developer.nvidia.com/blog/cuda-toolkit-12-8-delivers-nvidia-blackwell-support/)

## Historical runtime remediation

**Observed:** 2026-11-08. **Status: BLOCKED before model download and inference.**

- Built `oju-video-forge-comfyui:2.7.1-cuda12.8-0decb39` from the pinned
  PyTorch 2.7.1/CUDA 12.8 base digest and ComfyUI commit
  `0decb39ee5b5e17c200334589fc317dbbe98310e`. The resulting image is
  `sha256:bb40356ff2f10cdae5497a6f8c27ae488182be9aabea23e2604994b04d83e71d`
  and runs as UID/GID `10001:10001`.
- `docker build --pull=false -f services/comfyui/Dockerfile -t oju-video-forge-comfyui:2.7.1-cuda12.8-0decb39 .`
  exited 0.
- `docker run --rm --gpus all --entrypoint python oju-video-forge-comfyui:2.7.1-cuda12.8-0decb39 -c "import torch; assert torch.cuda.is_available(), 'CUDA unavailable'; device=torch.cuda.current_device(); name=torch.cuda.get_device_name(device); capability=torch.cuda.get_device_capability(device); x=torch.arange(1_000_000, device='cuda', dtype=torch.float32); result=torch.sin(x).sum(); torch.cuda.synchronize(); assert torch.isfinite(result).item(); print(f'torch={torch.__version__} cuda_build={torch.version.cuda} gpu={name} capability={capability} result={result.item():.3f}')"`
  exited 0: PyTorch `2.7.1+cu128`, CUDA build `12.8`, RTX 5070 Ti,
  capability `(12, 0)`, and CUDA tensor result `0.233`.

The official Comfy-Org Wan 2.2 TI2V-5B workflow template was reviewed at
commit `ea7faae6999fb31338f43408ff4a575bd3f7d9a0`. Its pinned model source
revision is `Comfy-Org/Wan-2.2@ee6f4a40737a995bf5818954cfce6d59443b0f04`;
the repository and official ComfyUI documentation identify the model assets
as Apache-2.0.

| Asset | Size (bytes) | SHA-256 |
|---|---:|---|
| `wan2.2_ti2v_5B_fp16.safetensors` | 9,999,658,848 | `456f901338bd9eadbded3828b819109a9b68e8a525ca5cf8d0049a69fcfeca1e` |
| `umt5_xxl_fp8_e4m3fn_scaled.safetensors` | 6,735,906,897 | `c3355d30191f1f066b26d93fba017ae9809dce6c627dda5f6a66eaa651204f68` |
| `wan2.2_vae.safetensors` | 1,409,400,960 | `e40321bd36b9709991dae2530eb4ac303dd168276980d3e9bc4b6e2b75fed156` |
| **Total** | **18,144,966,705** | |

No model assets were downloaded: explicit owner authorization was not obtained.
The intended destination is the private Docker volume
`oju-video-forge_models`, mounted at `/opt/ComfyUI/models`. ComfyUI was not
started. The official template has not been converted into the app's API
workflow format and `WAN_NODE_MAPPING` is not configured. Model loading, video
generation, FFprobe, checksum verification of an output, artifact registration,
and authorized retrieval remain blocked.
The pinned ComfyUI source declares several Python dependencies with open-ended
version constraints; a complete Python dependency lock was not produced in
this bounded pass.

References:

- [Official Wan 2.2 ComfyUI workflow and model guide](https://docs.comfy.org/tutorials/video/wan/wan2_2)
- [Pinned official TI2V-5B workflow template](https://github.com/Comfy-Org/workflow_templates/blob/ea7faae6999fb31338f43408ff4a575bd3f7d9a0/templates/video_wan2_2_5B_ti2v.json)
- [Pinned Comfy-Org Wan 2.2 model repository](https://huggingface.co/Comfy-Org/Wan-2.2/tree/ee6f4a40737a995bf5818954cfce6d59443b0f04)

## Historical initial model installation and startup attempt

**Observed:** 2026-11-08. **Status: BLOCKED before workflow validation or inference.**
This addendum supersedes earlier statements above that the approved models had
not been downloaded; the historical observations are retained.

Exactly the three owner-approved assets were installed in the private
`oju-video-forge_models` Docker volume from
`Comfy-Org/Wan-2.2@ee6f4a40737a995bf5818954cfce6d59443b0f04`. The private volume
manifest records matching authoritative and verified SHA-256 values; its model
directories are mode `0750`, and the model files/manifest are owned by ComfyUI
UID/GID `10001:10001` with mode `0640`.

| Asset | Verified bytes | Verified SHA-256 |
|---|---:|---|
| `wan2.2_ti2v_5B_fp16.safetensors` | 9,999,658,848 | `456f901338bd9eadbded3828b819109a9b68e8a525ca5cf8d0049a69fcfeca1e` |
| `umt5_xxl_fp8_e4m3fn_scaled.safetensors` | 6,735,906,897 | `c3355d30191f1f066b26d93fba017ae9809dce6c627dda5f6a66eaa651204f68` |
| `wan2.2_vae.safetensors` | 1,409,400,960 | `e40321bd36b9709991dae2530eb4ac303dd168276980d3e9bc4b6e2b75fed156` |
| **Total** | **18,144,966,705** | **PASS** |

The existing image was not rebuilt: tag
`oju-video-forge-comfyui:2.7.1-cuda12.8-0decb39`, image ID
`sha256:bb40356ff2f10cdae5497a6f8c27ae488182be9aabea23e2604994b04d83e71d`,
UID/GID `10001:10001`, with ComfyUI `--lowvram`. The Compose overlay was
validated as the `oju-video-forge` project, with the approved model volume,
GPU profile, and no published ComfyUI port. Startup command:

```powershell
docker compose --project-name oju-video-forge --env-file .env -f compose.yaml -f compose.gpu.yaml --profile gpu up -d --no-deps --no-build comfyui
```

The container entered a restart loop and was stopped without removing its
container or volumes. Startup logs report:
`RuntimeError: Failed to find C compiler. Please specify via CC environment variable.`
The final container state was `exited`, exit code `1`, `OOMKilled=false`. The
ComfyUI API never became ready; `/object_info` node-schema checks did not run.
The image lacks the compiler Triton requires during import. It was not rebuilt
because this task explicitly required preserving the already validated image.

No workflow was submitted and no GPU inference attempt was made. There is no
generated video, FFprobe result, or output checksum. The API/worker/frontend
integration changes remain unbuilt and unverified against the live stack; no
application GPU job or authorized artifact retrieval was exercised.

Container validation evidence:

| Exact command | Result |
|---|---|
| `docker build --target build -f frontend/Dockerfile -t oju-video-forge-frontend-build:phase4b frontend` | Exit 0; typecheck and production build passed |
| `docker run --rm oju-video-forge-frontend-build:phase4b npm test -- src/api/client.test.ts` | Exit 0; 31 tests passed |
| `docker run --rm oju-video-forge-api-test:phase4b python -m pytest -q -p no:cacheprovider tests/test_api_gpu.py tests/test_worker_gpu.py tests/test_review.py::test_comfy_generation_never_records_review_identity` | Two focused runs: each had 18 passed and 1 failed; after the second run, the test fixture was corrected to point the API at isolated artifact storage but was not rerun |
| Full application regression suite | Not run |

The separate browser-upload gate remains BLOCKED; this text-to-video path did
not use or attempt the denied synthetic upload fixture. No PR was created.

## Phase 4C startup and integrated generation

**Observed:** 2026-11-08. **Status: PASS for the requested Wan generation path.**

The existing Dockerfile was corrected by installing only the Debian `gcc`
runtime package, which supplies the C compiler Triton requires. The pinned
PyTorch 2.7.1/CUDA 12.8 base, pinned ComfyUI commit, UID/GID `10001:10001`,
private networks, security controls, and named volumes were preserved. No
compiler was installed on the Windows host, no model assets were downloaded
again, and the previous CUDA tensor test was not repeated.

| Exact command | Result |
|---|---|
| `docker build --pull=false -f services/comfyui/Dockerfile -t oju-video-forge-comfyui:2.7.1-cuda12.8-0decb39 .` | Exit 0; corrected image ID `sha256:b6a263ba21f9b17fc965e3facfd9093b633c814abdcec6bfbe59663787d7b7aa` |
| `docker compose --project-name oju-video-forge --env-file .env -f compose.yaml -f compose.gpu.yaml --profile gpu up -d --no-deps --no-build comfyui` | Exit 0; running, UID/GID `10001:10001`, `OOMKilled=false`, no host ports, original `oju-video-forge_models` volume mounted |
| `docker compose --project-name oju-video-forge --env-file .env -f compose.yaml -f compose.gpu.yaml --profile gpu exec -T comfyui python -` | Health endpoint 200; 1,047 nodes registered including all 10 required workflow nodes; all three installed assets visible at their verified sizes (18,144,966,705 bytes total) |
| `docker run --rm oju-video-forge-api-test:phase4c python -m pytest -q -p no:cacheprovider tests/test_api_gpu.py tests/test_worker_gpu.py tests/test_review.py::test_comfy_generation_never_records_review_identity` | Exit 0; 19 passed, one deprecation warning (before the final project-role regression was added) |
| `docker run --rm oju-video-forge-api-test:phase4c python -m pytest -q -p no:cacheprovider` | Exit 0; 240 passed, 12 skipped, five warnings (one full run; completed before the final project-role correction) |
| `docker run --rm oju-video-forge-api-test:phase4c python -m pytest -q -p no:cacheprovider tests/test_api_gpu.py` | Exit 0; 12 passed, one deprecation warning, after the project-role correction |
| `docker run --rm -e RUFF_CACHE_DIR=/tmp/ruff-cache oju-video-forge-api-test:phase4c ruff check app/api.py tests/test_api_gpu.py` | Exit 0; all checks passed |

The first of the two allowed authenticated API inference attempts loaded Wan 2.2
on `cuda:0` and completed all 20 sampler steps, but ComfyUI 0.44 rejected the
legacy nested `SaveVideo` dynamic-combo representation. The pinned workflow was
corrected to use its `format` and `format.codec` input paths. The second and
final inference attempt completed in 1.72 seconds with the model already loaded;
ComfyUI logs confirmed the RTX 5070 Ti CUDA device and Wan weights loaded, and
`nvidia-smi` observed 13,332 MiB in use during generation. No third attempt was
made.

The second job was submitted by an authenticated local project owner, persisted
in PostgreSQL, dispatched through Redis to the existing `gpu` RQ queue, executed
by the worker against private ComfyUI, registered as a project artifact, and
retrieved with HTTP 200 through the project-authorized API. FFprobe validated the
retrieved bytes:

| Model / workflow | Video | Authorized retrieval |
|---|---|---|
| Wan model revision `ee6f4a40737a995bf5818954cfce6d59443b0f04`; official workflow commit `ea7faae6999fb31338f43408ff4a575bd3f7d9a0`; submitted workflow SHA-256 `d0b553551ec130e4395b229e1d3f98c7164e17862686ce7b65e920a96db154eb` | 512x288; 2.125 seconds; H.264/MP4; 8 fps | 283,931 bytes; SHA-256 `fd030495349cf02d058809a06e1969ee6d70e0a695d136b078edbd31e74a664c` |

Project-owner submission and project-viewer denial tests passed. The React
generation interface, project-scoped artifact retrieval, API, dispatcher, and
worker images were built and started locally. Frontend typecheck/build and 31
focused client tests had passed before this task; frontend source was unchanged
after that validation. Browser-driven acceptance, PR CI/security scans, and
merge remain unverified. CPU rendering remains a separate worker path and was
not routed through ComfyUI.
