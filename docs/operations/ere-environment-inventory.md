# ERE Environment Inventory

**Observed:** 2026-11-05  
**Scope:** Read-only checks against the machine hosting this workspace. No remote hosts were contacted.

## Identity and boundary

| Item | Observation |
|---|---|
| Hostname | `ere` |
| Workgroup/domain | `WORKGROUP`; not domain-joined |
| ERE identity | Hostname is consistent with ERE, but the NuSass organizational assignment was not independently verified. |
| ARABA / AKOLE | Not contacted |

The host name and non-domain-joined state do not establish an AD/OIDC issuer. No identity-provider metadata was inspected.

## Hardware and operating system

| Item | Observation |
|---|---|
| OS | Windows 11 Enterprise, 10.0.26300, 64-bit |
| System | MSI US Desktop Aegis R2 |
| CPU | Intel Core Ultra 9 285, 24 cores / 24 logical processors |
| RAM | 31.38 GiB |
| GPU | NVIDIA GeForce RTX 5070 Ti |
| Driver | 610.88 |
| GPU memory | 16,303 MiB total; 2,054 MiB in use at observation time |
| CUDA | `nvidia-smi` reported CUDA UMD 13.3. A CUDA toolkit/runtime compatibility check was not completed. |

## Local runtime and services

| Item | Observation |
|---|---|
| Docker | Client/server 29.9.2; Docker Desktop Linux engine on WSL2, x86_64 |
| Docker Compose | 5.6.1 |
| Default / NVIDIA runtimes | `runc` / NVIDIA runtime available |
| Running containers | Existing local stack includes Traefik, Ollama, Open WebUI, Redis, PostgreSQL, SearXNG, and other services. No OJU Video Forge service health check was performed. |
| ComfyUI | `http://127.0.0.1:8188/` and `/system_stats` refused connections. No other host was queried. |
| Ollama | `http://127.0.0.1:11434` returned HTTP 200; version 0.40.14. |
| FFmpeg / FFprobe on host PATH | Neither executable was found. This does not establish whether the worker image or another private container includes them. |
| Reverse proxy | Traefik container maps host ports 8080→80 and 8443→443. No OJU Video Forge route was verified. |
| Docker networks | Observed `bridge`, `nusass-ai`, `temporal-network`, Swarm `ai-net` / `ingress`, and Docker Desktop extension networks. |

### Ollama model names observed

`qwen3.5:4b`, `qwen2.5-coder:14b`, `gemma4:12b`, `nimble-v3:latest`, `gpt-oss:latest`, `nimble:latest`, `nomic-embed-text:latest`, `qwen2.5-coder:1.5b-base`, `llama3.1:8b`, `gemma4:26b`, `qwen3-coder:30b`, `qwen3.6:27b`.

These are Ollama models, not evidence that a compatible video-generation model is installed.

## Storage

| Item | Observation |
|---|---|
| Windows local disk | C: NTFS, 1,832.8 GiB total, 766.4 GiB free |
| Docker inventory | 47 images (43.17 GB), 19 containers (205.8 MB), 15 local volumes (27.96 GB) |
| Docker data location / mounts | Not established; no volume was modified or removed. |

## Discovery commands and limitations

The read-only inventory used Windows CIM queries, `hostname`, `nvidia-smi`, `docker version`, `docker compose version`, `docker info`, `docker ps`, `docker port`, `docker network ls`, local HTTP GETs to the Ollama and ComfyUI endpoints, and `Get-CimInstance Win32_LogicalDisk`.

`nvidia-smi --query-gpu=cuda_version` was unsupported. The GPU driver and reported CUDA UMD value are observations only; toolkit compatibility, GPU workload stability, and ComfyUI operation remain unverified. No ERE-specific NAS or backup destination was established.
