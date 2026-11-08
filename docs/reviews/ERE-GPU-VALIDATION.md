# ERE GPU Validation

**Status:** BLOCKED / NOT VERIFIED  
**Observed:** 2026-11-05

## Hardware observation

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

## Current bounded readiness assessment

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
