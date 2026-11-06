# ADR-OVF-001 — Video Forge Service Architecture

**Status:** PROPOSED — NOT APPROVED
**Date:** 2026-11-05

## Decision Summary

OJU Video Forge is proposed as a **local-first AI video production platform** built on FastAPI + PostgreSQL. The architecture is intended to support:

- Traditional FFmpeg video rendering (CPU-only)
- AI-generated video via ComfyUI (Wan 2.2 TI2V-5B model, GPU-accelerated)
- Full project management with RBAC and audit trails
- OpenAPI v3.1 API contracts for external/agent integration

All features work without Azure dependencies. Azure integrations are extensible but not required.

## Architecture Components

### Core Services (apps/api/)

- FastAPI async framework with auto-generated OpenAPI spec
- Secure local authentication first; optional AD/OIDC integration later
- SQLAlchemy ORM with Alembic migrations
- RQ worker queue for distributed task processing
- PostgreSQL database for authoritative state
- Redis for job dispatch and rate limiting

### Workers (services/)

- **render-worker/**: FFmpeg container for deterministic video rendering
- **inference-worker/**: ComfyUI job dispatcher with volume-mounted models
- **media-processor/**: Audio processing, transcription, STTS narration

### Frontend (apps/web/)

- React + TypeScript + Vite build system
- shadcn/ui component library, WCAG accessible UI
- Video.js player for rendered output preview
- Role-based project management interface

## Security Model

**Local-first authentication; optional AD/OIDC integration later:**

- Local authentication must use securely hashed passwords and protected sessions
- Do not require an external identity provider for development
- Configure future OIDC integrations only from verified issuer and discovery metadata
- RBAC: Admin, Producer, Reviewer, Publisher, Viewer, Agent

Keep authentication behind a provider abstraction so optional AD/OIDC federation can
be added without making it a prerequisite for local use.

## URL and DNS Naming

| Purpose | Proposed name | Status |
| --- | --- | --- |
| Active Directory and internal infrastructure | `ojuidc.olutech.systems` | Existing identity namespace; infrastructure records are not verified here |
| Video Forge web application | `video.olutechsys.com` | Proposed application URL; DNS and TLS are not verified |
| Video Forge API and external integrations | `video-api.olutechsys.com` | Proposed API URL; DNS, TLS, and gateway are not verified |
| Local development web application | `http://localhost:5173` | Development target |
| Local development API | `http://localhost:8000` | Development target when locally exposed |

Do not use the AD domain as the application hostname or assume that its domain root
is an OIDC issuer. For AD/OIDC, configure the actual identity provider issuer and
discovery URL only after they have been verified. Keep database, Redis, ComfyUI, and
worker endpoints private on local/Compose networks.

## Infrastructure & Deployment

### Docker Compose (compose.yaml)

**Production mode (Phase B1+):**

```yaml
services:
  api-proxy: # Caddy reverse proxy with automatic HTTPS
    image: caddy:2-alpine
    volumes:
      - ./config/caddy.yml:/etc/caddy/config.yml

  api: # FastAPI on port 8000, internal only
    build: ./apps/api
    networks: [control]

  postgres: # PostgreSQL 17 with Alembic migration support
    image: postgres:17
    volumes: [pgdata:/var/lib/postgresql/data]

  redis: # Redis 7-alpine for job dispatch/rate limits
    image: redis:7-alpine

  ffmpeg-container: # FFmpeg in Docker container
    image: ffmpeg/libav-stable
    volumes:
      - ${FORGE_DATA}/artifacts:/artifacts
      - /dev/dri:/dev/dri:rwd

  inference-worker: # ComfyUI job dispatcher
    build: ./services/inference-worker
    gpus: [all]  # Requires GPU passthrough in Docker Desktop
    volumes:
      - ${FORGE_DATA}/models:/opt/ComfyUI/models
```

**Key constraints:**

- GPU memory limit: 70% of available VRAM (prevent crashloop)
- PyTorch expandable segments enabled (efficient offloading)
- Conservative model loading (FP8 fallback for Wan 2.2 if FP16 OOM)

### Volume Strategy

```yaml
services:
  inference-worker:
    volumes:
      - ${FORGE_DATA}/models:/opt/ComfyUI/models      # Wan 2.2 checkpoints
      - ${FORGE_DATA}/artifacts:/artifacts             # Rendered MP4s
      - ./config/workflows:/workflows:ro               # ComfyUI workflow JSONs

  comfyui: # optional, for browser debugging
    gpus: [all]
    volumes:
      - ${FORGE_DATA}/models:/opt/ComfyUI/models
```

## Implementation Gates & Timeline

| Gate | Completion Criteria | Target Phase | Status |
| --- | --- | --- | --- |
| A1 | GPU passthrough test passes, Wan 2.2 model loads in ComfyUI | B1 | ⏳ NOT VERIFIED |
| A2 | Alembic migrations successful on fresh PostgreSQL instance | B1 | ⏳ Migration files present; execution not verified |
| B1 | Secure local authentication and authorization work | B1 | ⏳ PENDING IMPLEMENTATION |
| C1 | FFmpeg container renders test clip with checksum verification | C1 | ⏳ FFmpeg integration pending |
| D1 | Wan 2.2 workflow generates MP4 via ComfyUI API + volume mount | D1 | ⏳ Primary development focus |
| E1 | End-to-end acceptance tests pass (login → project → render → download) | F1 | ⏳ After all above gates clear |

### Phase A1 — Architecture & Planning ⏳ PROPOSED

- [x] Initial requirements documented in `docs/implementation-plan.md`
- [ ] Accountable owner reviews and approves this ADR
- [ ] Technology and infrastructure assumptions validated
- [ ] Docker GPU passthrough tested; no successful GPU test is claimed

### Phase B1 — Foundation & Infrastructure ⏳ PENDING

The initial local development setup should remain independent of AD/OIDC. Any local
authentication implementation must be reviewed and tested before this ADR advances.

Other B1 tasks in parallel:

- Frontend React app scaffold (Vite + shadcn/ui)
- PostgreSQL schema creation via Alembic `upgrade head`
- Integration test fixtures for local authentication and authorization

### Phase C1 — Traditional Video Production

FFmpeg container will be started with `/dev/dri` volume mount to access WSL2 NVIDIA GPUs for CPU rendering (not GPU acceleration for FFmpeg, just direct disk I/O). Output verification uses FFprobe metadata comparison and SHA-256 checksum validation.

### Phase D1 — AI Video Generation

Primary development focus after B1 completion:

1. ComfyUI Docker build with Wan 2.2 checkpoint downloads pre-baked or via init script
2. Volume mount configuration for model weights (FP16 ~6GB footprint)
3. Inference job dispatcher API (`POST /api/v1/generations`)
4. Progress polling endpoint (`GET /jobs/{id}/status`)
5. Output reconciliation: ComfyUI output → MP4 extraction → artifact storage

**VRAM management:**

- Primary: Wan 2.2 TI2V-5B FP16 (~6GB VRAM)
- Fallback trigger: If `CUDA out of memory` error in worker logs, automatically use FP8 variant (UMT5 model)
- Automatic checkpoint offloading after job completion

### Phase E1 — Narration & Audio

Piper STTS integration for multilingual voice synthesis. SRT caption export via `faster-whisper`. Background music served from licensed MP3 pool (user-provided or platform catalog).

### Phase F1 — Integrations & Hardening

- Open WebUI internal client for chat session management
- Agent-facing API subset (`/api/v1/agents`) — controlled operations only
- PowerShell Universal adapter for remote execution (SSH/Wireguard)
- MCP protocol adapter for tool discovery
- Security audit + penetration testing pass
- PostgreSQL backup/restore drill

## Technology Stack Decisions

### Backend (apps/api/)

```yaml
Framework:      FastAPI 0.113+ (async-native, OpenAPI v3.1 auto-gen)
ORM:            SQLAlchemy 2.0 (type-safe async ORM)
DB:             PostgreSQL 17 (ACID compliance, JSONB)
Cache:          Redis 7-alpine (durable pub/sub, rate limiting)
Auth:           Local authentication initially; optional AD/OIDC adapter later
Workers:        RQ (Python-native persistent queues, better than Celery for local-first)
```

### Frontend Stack (apps/web/)

```yaml
Framework:      React 18+ TypeScript (type safety, ecosystem maturity)
Build Tool:     Vite (near-instant HMR, tree-shaking)
UI Library:     shadcn/ui (component-based, customizable Tailwind setup)
State Mgmt:     TanStack Query (built-in caching, retry logic)
Routing:        React Router v7 (type-safe routes, nested layouts)
Forms:          React Hook Form + Zod (validation-driven UX)
Video Player:   Video.js with ffmpeg.wasm fallback (cross-browser, WCAG accessible)
```

### Media Processing

- **FFmpeg:** Docker container mounted to `/dev/dri` for direct disk I/O access
- **ComfyUI:** Volume-mounted model weights + workflow JSON validation before execution
- **Piper:** Lightweight STTS, multilingual support (English default)
- **faster-whisper:** Optimized inference on WSL2 GPU (transcription to SRT/WebVTT)

### Infrastructure

- Docker Compose for local-first deployment (single CLI command: `docker compose up`)
- Caddy reverse proxy with automatic TLS (HTTPS for proxied endpoints only)
- Structured JSON logging + Prometheus metrics (optional Grafana dashboards for ops teams)

## Known Limitations & Gaps

### Must-Have Features ✅ In Scope

- OAuth2/OIDC authentication (JWT-based, Phase B1 priority)
- PostgreSQL database with Alembic migrations
- Docker Compose with optional GPU passthrough configuration
- FFmpeg integration for deterministic video rendering
- ComfyUI integration for Wan 2.2 AI video generation
- Media upload, timeline management, artifact verification
- Audit events + provenance manifest generation
- OpenAPI spec auto-generation from FastAPI

### Deferred Features (Future Phases)

| Feature | Target Phase | Rationale |
| --- | --- | --- |
| Azure Entra ID federation | Post A1 | Local-first requires local auth first, federation optional |
| Kubernetes/Container Apps deployment mode | Post E1 | Docker Compose works locally; AKS is production-scale upgrade |
| Multi-gigabyte file streaming upload (progressive) | Phase C2 | S3-compatible blob storage TBD, current: in-memory + filesystem |
| Real-time collaborative editing (WebRTC) | Future scope | Single-user timeline MVP first, multi-party collaboration later |

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| GPU passthrough instability (WSL2 + NVIDIA) | Medium | High | Conservative VRAM limits; automatic CPU fallback mode; monitor Docker logs for "GpuAllocationError" |
| Wan 2.2 VRAM overflow → crashloop | Medium | High | FP8 fallback logic; `--max-memory` flag in ComfyUI config; GPU usage monitoring via Nsight/ncu |
| Unauthorized access to generated artifacts | Low | Critical | RBAC + JWT claims validation; token expiration; audit logging for download events |
| Model licensing non-compliance (OpenAI API) | Medium | High | Use only open-weight models (Wan 2.2); document license terms in docs/; no OpenAI tokens required |
| ComfyUI workflow injection attack (untrusted nodes) | Low | Medium | Validate workflow JSON schema before execution; allowlist node IDs; sandbox worker container |

## Next Steps (before changing ADR status)

1. **Verify GPU passthrough** — Run a Docker GPU test to confirm WSL2 + NVIDIA configuration works

   ```bash
   wsl -e bash -c 'docker run --gpus all --rm nvidia/cuda:12.4-nvidia-cudart-dev/bin/nvsmi'
   ```

2. **Review and approve this ADR** — Keep status PROPOSED until an accountable owner explicitly approves it after validation
3. **Keep local development independent of AD/OIDC** — Treat identity federation as optional future integration

4. **Build Docker images** — Create API and inference-worker builds in CI-compatible format:

   ```bash
   docker build -f apps/api/Dockerfile --tag oju-video-forge:api .
   docker build -f services/inference-worker/Dockerfile --tag oju-video-forge:inference .
   ```

5. **First migration test** — Apply Alembic migrations to a fresh PostgreSQL instance

   ```bash
   docker compose run --rm api python -m alembic upgrade head
   ```

6. **Gate A1 completion** — Verify Wan 2.2 checkpoint loads in ComfyUI without VRAM errors

---

*Last Updated:* 2026-11-05
*Prepared By:* Principal Software Architect (AI Assistant)
*Approver:* Team Lead / Project Stakeholder
*Revision:* A1.2 — Domain naming documented; validation and approval pending
*Next Review:* Post Gate A1 completion or on major architectural change
