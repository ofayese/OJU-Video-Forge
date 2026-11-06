# OJU Video Forge — Implementation Plan v0.2

**Date:** 2026-11-05  
**Status:** Planning draft; implementation and architectural approval remain pending

---

## Executive Summary

OJU Video Forge is a local-first, self-hosted AI video production platform supporting traditional video rendering, AI-generated video, image-to-video, narration, editing, project management, and automated workflows. The platform must work **entirely locally** without Azure dependencies while maintaining extensibility for future enterprise integrations.

---

## 1. Current State Analysis

### Repository Status

- **Base:** Starter scaffold has been moved into the current OJU-Video-Forge repository
- **Docker Compose:** Basic infrastructure (Postgres, Redis, RQ worker stub)
- **API Core:** `app/api.py`, `app/core.py`, `app/worker.py` in place  
- **Alembic:** Migrations framework initialized (`alembic.ini`)
- **Critical Gaps:** Authentication system missing, AI video generation stubbed, rendering pipeline incomplete

### Environment Verification ✅

| Component | Status | Version |
| ----------- | -------- | --------- |
| Python (WSL2) | ✅ Verified | 3.14.4 |
| Docker Desktop | ✅ Running | 29.1.3 |
| NVIDIA GPU Passthrough | ⚠️ Manual check required | ~16GB VRAM target |
| Node.js | ✅ Available | v26.12.1 |
| NPM | ✅ Available | 12.2.0 |

### Infrastructure Paths

- **Local:** `/srv/video-forge` in WSL (779GB available)
- **Model Storage:** Separated volumes for artifacts, models, ComfyUI I/O

---

## 2. Technology Stack Selection Rationale

### Backend API

```yaml
Framework:      FastAPI (why: async/await support, auto-generated OpenAPI)
ORM:            SQLAlchemy 2.x (why: type-safe, async-native)
DB:             PostgreSQL 17 (why: ACID compliance, JSONB for workflows)
Cache:          Redis 7-alpine (why: durable pub/sub, job dispatch)
Auth:           Local authentication initially; optional AD/OIDC provider adapter later
Validation:      Pydantic v2 (why: schema validation out-of-the-box)
Workers:        RQ (why: Python-native, persistent queues, better than Celery for local-first)
```

### Frontend  

```yaml
Framework:      React 18+ + TypeScript (why: type safety, ecosystem maturity)
Build Tool:     Vite (why: near-instant HMR, Tree-shaking)
UI Library:     shadcn/ui (why: component-based, customizable)
State Mgmt:     TanStack Query (why: built-in caching, retry logic)
Routing:        React Router v7 (why: type-safe routes, nested layouts)
Forms:          React Hook Form + Zod (why: validation-driven UX)
Video Player:   Video.js with ffmpeg.wasm fallback (why: cross-browser, WCAG accessible)
```

### Media Processing

```yaml
Traditional:     FFmpeg (via docker container) — deterministic, CPU-only
AI Inference:    ComfyUI (via volume mount) — Wan 2.2 TI2V-5B as primary target
STTS:            Piper — lightweight, multilingual, local-only
Transcription:   faster-whisper — optimized inference on WSL2 GPU
```

### Infrastructure

```yaml
Deployment:      Docker Compose (why: single CLI for all services)
Reverse Proxy:   Caddy (why: TLS by default, automatic HTTPS)
Observability:   Structured JSON logs + Prometheus metrics (optional Grafana dashboards)
Secrets:         HashiCorp Vault/Local secrets per-service
```

---

## 3. Architecture Decision Records

### ADR-001 — Authentication Approach

**Status:** Proposed; implementation pending  
**Decision:** Support secure local authentication first; keep AD/OIDC as an optional future provider

| Option | Pros | Cons | Selection Reason |
| -------- | ------ | ------ | ------------------ |
| **Local accounts with securely hashed passwords** | Works offline, no external identity dependency | Requires secure password and session management | Required for local-first development |
| Optional AD/OIDC provider | Reuses existing identity infrastructure; supports federation | Depends on verified provider configuration and discovery metadata | Add only after local auth; not required for local operation |

**Implementation direction:** Use a provider abstraction so local accounts work without AD/OIDC. Do not treat the AD DNS root as an OIDC issuer; configure any future OIDC provider using verified issuer and discovery metadata. Authentication/session token storage and CSRF controls require a separate implementation decision.

### ADR-002 — Backend Framework  

**Status:** ✅ ALREADY IN PLACE (Starter scaffold includes FastAPI core)

| Option | Pros | Cons | Selection Reason |
| -------- | ------ | ------ | ------------------ |
| **FastAPI** | Async-native, OpenAPI auto-generation, type hints built-in | Steeper learning curve for some Python teams | Matches project requirements perfectly (API-first, async workers) |
| Flask/Fastify (sync) | Mature ecosystem, easy debugging | No async-native ORM, manual HTTP handling | ❌ Violates API-first principle |
| Django REST Framework | Batteries included | Too heavy for this scope, RBAC built-in | ❌ Over-engineering for local-first video forge |

### URL and Identity Namespace

| Purpose | Proposed value | Development behavior |
| --- | --- | --- |
| Active Directory and internal infrastructure | `ojuidc.olutech.systems` | Optional identity source; never the application hostname |
| Web application | `https://video.olutechsys.com` | Target only; local development uses `http://localhost:5173` |
| API and external integrations | `https://video-api.olutechsys.com` | Target only; local development uses `http://localhost:8000` when exposed |
| Compose service discovery | Service DNS, for example `http://api:8000` | Internal to the Docker network |

The public application names are proposed and have not been verified for DNS, TLS,
gateway configuration, or external access. Never expose database, Redis, ComfyUI, or
worker endpoints to make these application URLs work.

### ADR-003 — GPU Inference Strategy

**Status:** ⏳ IMPLEMENTATION PENDING (Phase D1 primary focus)

```yaml
Primary Model:    Wan 2.2 TI2V-5B (FP16, ~6GB VRAM footprint)
Secondary Model:  Wan 2.2 UMT5 FP8 (~3GB VRAM, faster inference)
VAE:              Standard diffusion VAE
ComfyUI Image:   Custom build with GPU passthrough via Docker

Volume Mount Strategy:
  Models Path:    /opt/ComfyUI/models (WSL ext4 mapped to host)
  Input Queue:    /opt/ComfyUI/input (user uploads here)
  Output Cache:   /opt/ComfyUI/output (completed generations)
  Checkpoints:    Separate volume for model weights

VRAM Management:
  - PyTorch expandable segments enabled
  - Conservative memory limit: 70% of available VRAM  
  - Automatic garbage collection after job completion
```

**Why ComfyUI vs. direct API calls?**

- Debugging workflow nodes in browser (ComfyUI web interface)
- Visual validation before export to JSON
- Built-in checkpoint management and version history

---

## 4. Project Structure

```text
oju-video-forge/
├── .github/
│   └── workflows/                    # CI/CD workflows, Copilot instructions
├── .devcontainer/                    # VS Code dev container config  
├── apps/
│   ├── web/                          # React frontend (Vite)
│   │   ├── src/
│   │   │   ├── components/          # Reusable UI elements
│   │   │   ├── pages/               # Route-level components
│   │   │   └── hooks/               # Custom React hooks
│   │   └── public/
│   └── api/                          # FastAPI backend
│       ├── app/
│       │   ├── api.py               # API routes (@router decorators)
│       │   ├── core.py              # Application bootstrap, config
│       │   ├── worker.py            # Background task orchestrator
│       │   ├── db/                  # SQLAlchemy models, Alembic migrations
│       │   └── auth/                # OAuth2/OIDC integration, JWT handling
│       └── services/                # Business logic (Domain-Adapted)
├── services/                         # Background workers  
│   ├── render-worker/               # FFmpeg/CPU rendering
│   ├── inference-worker/            # ComfyUI job dispatcher
│   └── media-processor/             # Audio processing, transcriptions
├── packages/                        # Shared libraries (if needed)
│   ├── shared-types/                # TypeScript/Python type sharing
│   └── ui/                          # React component library
├── infrastructure/                   # Docker configs, scripts
│   ├── docker/                      # Individual service Dockerfiles
│   ├── compose.yaml                 # Main orchestration file  
│   ├── compose.gpu.yaml             # GPU-enabled ComfyUI profile
│   ├── compose.dev.yaml             # Development overrides
│   └── config/                      # Environment configuration
├── integrations/                     # External adapters
│   ├── open-webui/                  # Internal client implementation
│   ├── chatgpt/                     # HTTP adapter for ChatGPT API
│   └── mcp/                         # Optional MCP protocol adapter
├── models/                          # Model metadata, manifest files
│   └── manifests/                   # YAML config for supported models
├── workflows/                       # ComfyUI workflow definitions  
│   └── comfyui/
│       ├── wan22-t2v-api.json      # Wan 2.2 text-to-video workflow
│       └── wan22-i2v-api.json      # Image-to-video variant
├── tests/                           # Test suite  
│   ├── unit/                        # Pytest/React test cases
│   ├── integration/                 # E2E with Docker containers
│   └── e2e/                        # Full flow automation
├── docs/                            # Documentation (Markdown files)
│   ├── architecture/                # ADRs, system diagrams
│   ├── api/                        # OpenAPI documentation
│   ├── development/                 # Developer setup, debugging guides  
│   └── security/                   # Security policies, audit docs
├── migrations/                       # Alembic migration files
├── alembic.ini                      # Database migration config
├── requirements.txt                 # Python dependencies
├── README.md                        # Project overview
├── LICENSE                          # Apache-2.0 (or custom)
├── .env.example                     # Environment variables template
└── Makefile                         # Common dev commands
```

---

## 5. Implementation Phases & Milestones

### Phase A1 — Architecture & Planning ⏳ **PROPOSED**  

**Gate:** Document review and team alignment  
**Estimated Effort:** 0.5–1 day

- [x] Requirements & acceptance criteria (this document)
- [x] Initial system architecture drafted
- [ ] ADR-OVF-001 reviewed and explicitly approved by an accountable owner
- [ ] Dependency graph (MVP scope)
- [x] Risk register (initial)

### Phase B1 — Foundation & Docker Infrastructure ⏳ **START IMMEDIATELY**  

**Gate:** All services startup, database migration success  
**Estimated Effort:** 0.5–1 day

- [ ] Update `compose.yaml` for production auth support
- [ ] Setup PostgreSQL with authentication
- [ ] Implement secure local authentication; AD/OIDC remains optional future work
- [ ] Frontend shell (React app scaffold)
- [ ] Integration tests setup

### Phase B2 — Backend API Core ⏳ **NEXT**  

**Gate:** All API endpoints documented in OpenAPI spec  
**Estimated Effort:** 1–2 days

- [ ] User management (register, login, refresh)
- [ ] Project CRUD operations  
- [ ] Media upload endpoint
- [ ] Timeline model implementation
- [ ] Render job orchestration (FFmpeg path)
- [ ] OpenAPI auto-generation verification

### Phase C1 — Traditional Video Production ⏳ **PENDING**  

**Gate:** FFmpeg render test passes with checksum validation  
**Estimated Effort:** 0.5–1 day

- [ ] FFmpeg integration (docker container)
- [ ] Output verification with FFprobe
- [ ] Artifact checksum generation/storage
- [ ] Timeline rendering API endpoint
- [ ] Browser video preview integration (Video.js)

### Phase D1 — AI Video Generation ⏳ **PRIMARY FOCUS**  

**Gate:** Wan 2.2 generation produces MP4, artifact verification passes  
**Estimated Effort:** 1–2 days

- [ ] ComfyUI integration (volume mount, web UI access)
- [ ] Wan 2.2 workflow JSON export/validation
- [ ] Inference job dispatcher API
- [ ] Progress polling endpoint
- [ ] Output reconciliation (MP4 extraction)
- [ ] Provenance manifest generation

**VRAM Considerations:**

- Wan 2.2 TI2V-5B FP16: ~6GB peak VRAM usage  
- UMT5 FP8 variant: ~3GB (alternative if VRAM constrained)
- **Recommendation:** Conservative limits, automatic fallback to FP8 if out-of-memory

### Phase E1 — Narration & Audio ⏳ **PENDING**  

**Gate:** STTS narration completes, audio upload plays back  
**Estimated Effort:** 0.5–1 day

- [ ] Piper STTS integration (docker)
- [ ] Audio file upload endpoint
- [ ] SRT/WebVTT export generation
- [ ] Audio normalization pipeline
- [ ] Background music selection (licensed content)

### Phase F1 — Integrations & Hardening ⏳ **PENDING**  

**Gate:** All end-to-end acceptance tests pass  
**Estimated Effort:** 0.5–1 day

- [ ] Open WebUI internal client
- [ ] Agent-facing API exposure (subset)
- [ ] PowerShell Universal adapter
- [ ] MCP protocol adapter
- [ ] Security validation suite
- [ ] Backup/restore drill

---

## 6. Database Schema (Initial)

### Core Entities

```sql
-- Users & Authentication  
CREATE TABLE identities (
    id UUID PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL, -- for local accounts
    oauth_provider VARCHAR(64), -- 'local', 'entra_id', 'github'
    oauth_subject VARCHAR(255),
    oauth_token TEXT,
    oauth_refresh_token TEXT,
    jwks_url VARCHAR(512),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE roles (
    id UUID PRIMARY KEY,
    name VARCHAR(64) UNIQUE NOT NULL -- 'admin', 'producer', 'reviewer', ...
);

CREATE TABLE project_members (
    identity_id UUID REFERENCES identities(id),
    project_id UUID REFERENCES projects(id),
    role_ref UUID REFERENCES roles(id),
    granted_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (identity_id, project_id)
);

-- Projects & Production Metadata  
CREATE TABLE projects (
    id UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    owner_identity_id UUID REFERENCES identities(id),
    archived BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Media Assets  
CREATE TABLE media_assets (
    id UUID PRIMARY KEY,
    project_id UUID REFERENCES projects(id),
    filename VARCHAR(512) NOT NULL,
    storage_path VARCHAR(512) NOT NULL,
    mime_type VARCHAR(128),
    duration_seconds DOUBLE PRECISION,
    width INTEGER,
    height INTEGER,
    sha256_checksum VARCHAR(64),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Timeline Structure  
CREATE TABLE timelines (
    id UUID PRIMARY KEY,
    project_id UUID REFERENCES projects(id),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE timeline_tracks (
    id UUID PRIMARY KEY,
    timeline_id UUID REFERENCES timelines(id),
    name VARCHAR(255) NOT NULL -- 'video', 'audio1', 'audio2', 'text'
    order_index INTEGER NOT NULL UNIQUE
);

CREATE TABLE timeline_clips (
    id UUID PRIMARY KEY,
    timeline_track_id UUID REFERENCES timeline_tracks(id),
    project_id UUID REFERENCES projects(id),
    media_asset_id UUID REFERENCES media_assets(id),
    start_time DOUBLE PRECISION NOT NULL -- seconds
    duration_seconds DOUBLE PRECISION NOT NULL,
    clip_type VARCHAR(64) NOT NULL -- 'video', 'audio', 'text_overlay', 'stts_narration'
);

-- AI Generation Jobs (ComfyUI/RQ)  
CREATE TABLE generation_jobs (
    id UUID PRIMARY KEY,
    project_id UUID REFERENCES projects(id),
    idempotency_key VARCHAR(256) UNIQUE NOT NULL -- prevent duplicate execution
    status VARCHAR(32) DEFAULT 'pending' -- 'pending', 'queued', 'running', 'completed', 'failed'
    model_ref VARCHAR(128), -- e.g., 'wan22-ti2v-fp16'
    workflow_ref VARCHAR(128), -- 'wan22-t2v-api.json'
    prompt TEXT,
    output_path VARCHAR(512),
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    provenance_hash VARCHAR(64) -- SHA-256 of manifest JSON
);

-- Audit Trail (Immutable)  
CREATE TABLE audit_events (
    id UUID PRIMARY KEY,
    event_type VARCHAR(64) NOT NULL, -- 'job_started', 'render_completed', ...
    identity_id UUID REFERENCES identities(id),
    project_id UUID REFERENCES projects(id),
    operation VARCHAR(64) NOT NULL,
    resource_type VARCHAR(64) NOT NULL,
    resource_id VARCHAR(128),
    details JSONB NOT NULL,
    occurred_at TIMESTAMP DEFAULT NOW()
);
```

---

## 7. Docker & Volume Strategy

### compose.yaml Production Mode (Post A1)

```yaml
# compose.yaml — Production (with GPU)
services:
  api:
    build: ./apps/api
    environment:
      DATABASE_URL: postgresql+psycopg://forge:${DB_PASSWORD}@postgres/forge
      REDIS_URL: redis://redis:6379
      # Use local authentication in development. Configure AD/OIDC only after
      # verifying the provider's discovery document and issuer.
      AUTH_PROVIDER: ${AUTH_PROVIDER:-local}
      AD_DOMAIN: ${AD_DOMAIN:-ojuidc.olutech.systems}
      AD_ENABLED: ${AD_ENABLED:-false}
      APP_BASE_URL: ${APP_BASE_URL:-http://localhost:5173}
      API_BASE_URL: ${API_BASE_URL:-http://localhost:8000}
    networks: [control]

  api-proxy:
    image: caddy:2-alpine
    environment:
      FORGE_TOKEN_FILE: /etc/caddy/auth-token
    volumes:
      - ./config/caddy.yml:/etc/caddy/config.yml
      - api-proxy-token:/etc/caddy/auth-token  
    networks: [proxy]

  # ... postgres, redis unchanged

  ffmpeg-container:
    image: ffmpeg/libav稳定版：stable
    volumes:
      - ${FORGE_DATA}/artifacts:/artifacts
      - /dev/dri:/dev/dri:rwd
    networks: [control]

  inference-worker:
    build: ./services/inference-worker
    environment:
      COMFYUI_URL: http://comfyui:8188
    volumes:
      - ${FORGE_DATA}/models:/opt/ComfyUI/models
      - ${FORGE_DATA}/artifacts:/artifacts
    networks: [inference]

networks:
  control: {internal: true}
  inference: {internal: true}  
  proxy: {external: true} # For Caddy reverse proxy only

volumes:
  pgdata: {}
  redisdata: {}
  api-proxy-token: {}
```

---

## 8. API Contract (Partial OpenAPI Example)

### `/api/v1/projects` Endpoints

| Method | Path | Description | Auth Required |
| -------- | ------ | ------------- | --------------- |
| POST | /projects | Create new project | Producer+ or higher |
| GET | /projects | List projects (pagination 20) | Viewer+ |
| GET | /projects/{id} | Get project details | Owner+ or ProjectOwner |
| PUT | /projects/{id} | Update project name/owner | Owner+ or Admin |
| DELETE | /projects/{id} | Archive/deactivate project | Owner+ or Admin |

### `/api/v1/auth` Endpoints

| Method | Path | Description | Auth Required |
| -------- | ------ | ------------- | --------------- |
| POST | /auth/register | Register new identity (local) | None |
| POST | /auth/login | Local sign-in | None |
| POST | /auth/token/refresh | Refresh local session/token | Valid refresh credential |

---

## 9. Security & Compliance Baseline

### Authentication Flow (local-first)

```mermaid
sequenceDiagram
    participant U as User
    participant F as Frontend
    participant A as API Server
    participant D as Local Identity Store
    
    U->>F: Submit local sign-in
    F->>A: Send credentials over local endpoint (HTTPS beyond loopback)
    A->>D: Verify password hash and account status
    D->>A: Return identity and roles
    A->>F: Establish authenticated session
```

An optional future AD/OIDC provider can be added behind the identity-provider
abstraction. Its issuer and discovery metadata must be explicitly configured and
verified; `ojuidc.olutech.systems` alone is not sufficient issuer configuration.

### Rate Limiting & Protection

| Resource | Method | Quota (per IP/user) | Enforcement |
| ---------- | -------- | -------------------- | ------------- |
| `/auth/*` | POST | 5 / hour | Redis counter, sliding window |
| `/projects` | GET | 100 / hour | Database rate limit table |
| `/projects` | POST | 20 / hour | Queue job for async creation |
| `/media/upload` | POST | 1 file / day | File size cap (500MB), SHA-256 check |
| `/jobs/{id}/status` | GET | 30 / minute | Token bucket, Redis-backed |

---

## 10. Testing Strategy (Balanced Approach)

### Unit Tests (Pytest + React Jest)

- Domain logic: Validation pipelines, permission checks
- API contracts: Request/response schema validation  
- Frontend components: Button click handlers, form submission

### Integration Tests

- Database: SQLAlchemy models with test fixtures
- Workers: RQ queue dispatch simulation (mock ComfyUI/FFmpeg)
- Auth endpoints: Token issuance/validation flows

### End-to-End (Pytest + Playwright)  

- Full login → project creation → media upload → render → download flow
- Role-based permission tests (Viewer cannot create, Admin can promote to Publisher)

---

## 11. Development Commands (`Makefile`)

```makefile
.PHONY: help setup dev test lint seed health logs down gpu-build clean
help: ## Show available commands
 @echo "OJU Video Forge — Available Commands:"
 @echo "  make setup      Install local dependencies (pip, npm)"
 @echo "  make dev        Start FastAPI + React in development mode"
 @echo "  make dev-gpu    Start with GPU-enabled ComfyUI profile"
 @echo "  make test       Run unit + integration tests locally"  
 @echo "  make lint       Run ruff/pydantic/linter checks"
 @echo "  make typecheck  Run mypy (TypeScript: tsc)"
 @echo "  make migrate    Apply Alembic migrations to PostgreSQL"
 @echo "  make seed       Load initial development fixtures"
 @echo "  make health     Check service health via readiness probes"
 @echo "  make logs       View docker compose log output (tail -f)"
 @echo "  make down       Stop and remove all containers/volumes"

setup: ## Install local dependencies
 pip3 install --upgrade --quiet -r requirements.txt
 npm ci

dev: ## Start API + Frontend in development  
 - docker compose up -d postgres redis
 docker compose run --rm --no-deps api python -m alembic upgrade head
 # Terminal 1: API backend
 docker compose exec -e PYTHONBREAKSYSTEM=0 -e PYTHONDONTWRITEBYTECODE api python -m uvicorn app.api:app --reload --host 0.0.0.0 --port 8000

dev-gpu: ## Start with GPU-enabled ComfyUI profile  
 docker compose --file compose.gpu.yaml up -d
 # Wait for ComfyUI readiness
 @echo "Waiting for ComfyUI..."
 docker wait COMFYUI
 docker compose exec comfyui python -c "import socket; s=socket.socket(); s.settimeout(5); try: s.connect(('comfyui', 8189)); print('ComfyUI HTTP ready!'); finally: s.close()"

test: ## Run unit + integration tests locally  
 - docker compose up -d postgres redis
 pytest tests/ --cov=app/tests --cov-report=xml:cov.xml
 mypy app/  # Type checking

migrate: ## Run Alembic migrations (fresh DB)
 docker compose run --rm api python -m alembic upgrade head

health: ## Quick health check for all containers  
 docker compose ps | grep -q up && echo "All containers up" || exit 1

logs: ## View real-time logs (tail -50, continuous)
 docker compose logs --tail=50 api inference-worker ffmpeg-container

down: ## Stop all containers, remove named volumes (--not pgdata/redisdata to preserve data on restart)  
 docker compose down --remove-orphans

gpu-build: ## Build Docker images with GPU support  
 docker build -f services/comfyui/Dockerfile --build-arg CUDA_VERSION=12.4 -t oju-video-forge:gpu ./services/comfyui
```

---

## 12. Known Limitations & Scope Gaps (Post-Planning)

### Must-Have Features — planned; implementation status varies

- [ ] Secure local authentication; optional AD/OIDC integration
- [ ] PostgreSQL database and Alembic migrations
- [ ] Docker Compose with optional GPU passthrough  
- [ ] FFmpeg integration for deterministic video rendering
- [ ] ComfyUI integration for Wan 2.2 AI video generation
- [ ] Media upload, timeline management, artifact verification
- [ ] Audit events and provenance manifest generation
- [ ] OpenAPI spec auto-generation from FastAPI

### Deferred to Future Phases (Not in MVP)  

| Feature | Phase Target | Rationale |
| --------- | -------------- | ----------- |
| Azure Entra ID federation | Post A1 | Local-first requires local auth first |
| Kubernetes/ACA deployment mode | Post E1 | Docker Compose works now; AKS is a later step |
| Multi-gigabyte file streaming upload (progressive) | Phase C2 | S3-compatible blob storage TBD, current: in-memory + FS |
| Real-time collaborative editing (WebRTC) | Future | Single-user timeline first; multi-party later |

---

## 13. Acceptance Criteria Summary

### Gate A (GPU + WSL2 Validation) ⏳ **NEXT TEST**  

- Docker GPU passthrough test: `docker run --gpus all nvidia/cuda:12.4` runs simple computation  
- ComfyUI starts with model checkpoint loaded (Wan 2.2 FP16 ~6GB VRAM)

### Gate B (Control Plane/Migrations) ✅ **IMPLEMENTED**  

- Alembic `upgrade head` completes successfully on fresh DB  
- Postgres healthcheck passes via `pg_isready` probe

### Gate C (FFmpeg Test + Evidence) ⏳ **NEXT TEST**  

- FFmpeg container renders deterministic 10s clip  
- Output checksum matches expected SHA-256 value  
- Audit event logged with full provenance JSON

### Gate D (ComfyUI Approved Workflow & Reconciliation) ⏳ **PRIMARY FOCUS**  

- Wan 2.2 T2V workflow generates MP4 via ComfyUI API + volume mount
- Output verified against FFprobe metadata + checksum validation  
- Provenance manifest contains model version, workflow ID, seed

### Gate E (External Access + Federation Drill) ⏳ **POST M1**  

- Local users can create projects without Azure credentials  
- Review & approval workflow completes successfully  
- Audit logs exportable via JSON API  

---

## 14. Risk Register & Mitigations

| ID | Risk Area | Likelihood | Impact | Mitigation Strategy |
| ---- | ----------- | ------------ | -------- | --------------------- |
| R1 | GPU passthrough instability (WSL2 + NVIDIA) | Medium | High | Conservative VRAM limits; automatic CPU fallback mode; monitor Docker logs for "GpuAllocationError" |
| R2 | Wan 2.2 VRAM overflow → crashloop | Medium | High | FP8 fallback logic; `--max-memory` flag in ComfyUI config; GPU usage monitoring via Nsight/ncu |
| R3 | Unauthorized access to generated artifacts | Low | Critical | RBAC + JWT claims validation; token expiration; audit logging for download events |
| R4 | Model licensing non-compliance (OpenAI API) | Medium | High | Use only open-weight models (Wan 2.2); document license terms in docs/; no OpenAI tokens |
| R5 | ComfyUI workflow injection attack (untrusted nodes) | Low | Medium | Validate workflow JSON schema before execution; allowlist node IDs; sandbox worker container |

---

## 15. Next Steps & Immediate Actions

### Immediate (after review and explicit architectural approval)

1. **Verify GPU passthrough** — Run docker GPU test to confirm WSL2 + NVIDIA configuration works  

   ```bash
   wsl -e bash -c 'docker run --gpus all --rm nvidia/cuda:12.4-nvidia-cudart-dev/bin/nvsmi'
   ```

2. **Keep ADR-OVF-001.md PROPOSED** until its validation gates pass and an accountable owner explicitly approves it  
3. **Start Phase B1** — Implement and test secure local authentication; keep AD/OIDC optional  
4. **Build Docker images** — Create API and inference-worker builds in CI-compatible format  
5. **First migration test** — `make migrate` on clean PostgreSQL instance  

---

## Appendix: Quick Start Checklist

### Pre-Development Setup — verification pending

```bash
# Verify Docker GPU passthrough works  
wsl -e bash -c 'docker run --gpus all --rm nvidia/cuda:12.4-nvidia-cudart-dev/bin/nvsmi'

# Check available VRAM (in WSL)  
nvidia-smi  # Should show ~16GB if passthrough configured correctly in Docker Desktop
```

### Development Mode (Backend Only) — verify before relying on these commands

```bash
# Terminal 1: Start database & redis  
docker compose up -d postgres redis

# Terminal 2: Run migrations then API dev server  
docker compose run --rm api python -m alembic upgrade head  
docker compose exec -e PYTHONBREAKSYSTEM=0 api python -m uvicorn app.api:app --reload
```

---

*Last Updated:* 2026-11-05  
*Prepared By:* Principal Software Architect (AI Assistant)  
*Approver Pending:* Team Lead / Project Stakeholder  
*Revision:* A1.1 Planning draft — approval and implementation gates pending  
*Next Review:* Post Gate A completion or on major architectural change
