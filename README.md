# OJU Video Forge

**Experimental; not production ready.** Both architecture ADRs remain PROPOSED.

Docker-first, local-first authenticated project rendering: local password sessions,
project membership, bounded private media uploads, PostgreSQL-backed jobs and
dispatch outbox, Redis/RQ workers, CPU FFmpeg timelines, verified H.264/AAC MP4
artifacts, and a React frontend using authenticated API responses.

Optional ComfyUI/GPU generation is a separate, unverified acceptance milestone.
No AD domain join, Azure service, external Docker network, or public deployment
is required for deterministic rendering.

## Local Docker environments

| Purpose | Compose project | Frontend |
|---|---|---|
| Main application | `oju-video-forge` | `http://127.0.0.1:5173/` |
| Acceptance tests | `oju-video-forge-acceptance` | `http://127.0.0.1:5174/` |
| Integration tests | `oju-video-forge-integration` | `http://127.0.0.1:5175/` |

Use Docker Desktop with Linux containers/WSL2 and Compose 2.24.4+. Application
Python, Node, npm, FFmpeg, PostgreSQL, and Redis stay inside containers.
Every project uses separate secrets, named volumes, and private networks.

Read the [Docker environment and reconciliation runbook](docs/operations/DOCKER-ENVIRONMENTS.md)
before startup, especially when existing test projects or volumes are present.
Owner-approved data disposition, verified backups, and port checks precede any
replacement or migration. Never automatically transfer test users into the main
database, rename managed volumes, or delete potentially valuable data.

After approval and creation of the private `.env` with a unique random database
secret:

```powershell
.\scripts\forge.ps1 -Environment application build
.\scripts\forge.ps1 -Environment application up -d --wait
.\scripts\forge.ps1 -Environment application ps
.\scripts\forge.ps1 -Environment application stop
```

The frontend proxies to its own API; PostgreSQL, Redis, and ComfyUI have no public
ports. `migrate` is a checked one-shot service, not an assumption based on database
startup. Administrator bootstrap prompts privately through `python -m app.accounts`;
no default credentials are provided.

## Documentation and evidence

- [Docker-first development and tests](docs/development/DEVELOPMENT-WORKFLOW.md)
- [Authentication and projects](docs/authentication-projects-v1.md)
- [Timeline rendering contract](docs/rendering-contract-v1.md)
- [Vertical-slice plan](docs/implementation/VERTICAL-SLICE-PLAN.md)
- [Docker naming reconciliation](docs/reviews/DOCKER-NAMING-RECONCILIATION.md)
- [Current acceptance register](docs/reviews/ERE-ACCEPTANCE-REGISTER.yaml)
- [Definition of Done](docs/development/DEFINITION-OF-DONE.md)
- [Security policy](SECURITY.md)

Configuration validation, unit tests, or GPU visibility do not establish end-to-end
release readiness. Check the evidence register for actual runtime results and
remaining blockers.
