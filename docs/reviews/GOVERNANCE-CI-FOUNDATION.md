# Governance and CI foundation evidence

## Baseline and scope

- Repository: `ofayese/OJU-Video-Forge`
- Starting branch: `main`
- Starting commit: `8e462ec233c3cecc1fcf225a0f02e0d551ef50f8`
- Feature branch: `chore/ovf-governance-ci-foundation`
- PRs #3 and #4 were confirmed merged into the starting commit.
- The GitHub Dependabot open-alert query returned `0`.
- No existing issues or milestones were found before this backlog was created.
- `main` has no branch-protection rule. No repository settings were changed.
- Project locks resolve FastAPI 0.162.0, Starlette 1.10.0,
  python-multipart 0.0.31, pytest 9.0.3, and Vitest 4.1.12. The ERE global Python
  installation is separate and must not be used in place of the project lock.

Work is limited to repository governance, CI, local validation, dependency
reproducibility, public documentation, and implementation planning. No application
service was deployed, no shared database was migrated, and no external environment,
directory, cloud service, or GPU workload was accessed.

## Implemented foundation

- Added project-wide Copilot instructions, five scoped instruction files, and four
  focused custom agents using supported VS Code formats.
- Added four least-privilege GitHub Actions workflows for backend, frontend,
  infrastructure, and security checks. Third-party actions use immutable commit
  references.
- Added issue forms, pull request template, CODEOWNERS, Dependabot policy,
  contribution/security policies, development guidance, and evidence-handling
  policies.
- Added hash-locked Python runtime and developer requirements. A development audit
  identified `PYSEC-2026-1845` in pytest 8.4.2; the dev requirement and lock now use
  pytest 9.0.3.
- Added acceptance-register schema/validation, repository metadata validation,
  OpenAPI export, and dependency-lock consistency checks.
- Added a dependency-linked backlog of issues [#5–#16](https://github.com/ofayese/OJU-Video-Forge/issues/5),
  grouped under 11 GitHub milestones. The CPU rendering slice is not blocked by
  optional GPU, captions, review, or agent milestones.
- Sanitized current public environment summaries. Historical Git objects were not
  rewritten.

## Local validation on ERE

All commands below were run on 2026-11-06 unless noted. The Python checks used the
project `.venv` and committed hash lock, not the global system Python.

| Check | Command | Result |
|---|---|---|
| Python environment | `python -m pip check` | Exit 0; no broken requirements |
| Runtime dependency audit | `pip-audit -r requirements.lock.txt` | Exit 0; no known vulnerabilities |
| Developer dependency audit | `pip-audit -r requirements-dev.lock.txt` | Exit 0; no known vulnerabilities after the pytest update |
| Python lint | `ruff check app tests alembic scripts` | Exit 0 |
| Python compilation | `python -m compileall -q app tests alembic scripts` | Exit 0 |
| Python tests | `python -m pytest -q -p no:cacheprovider` | Exit 0; 63 passed, 1 skipped, 1 warning |
| OpenAPI | `python scripts/export_openapi.py --output <temp>/openapi.json` | Exit 0; generated and validated |
| Acceptance register | `python scripts/validate_acceptance_register.py` | Exit 0 |
| Repository metadata | `python scripts/validate_repository.py` | Exit 0 |
| Python lock consistency | `python scripts/check_dependency_locks.py` | Exit 0 |
| Workflow lint | `go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.12` | Exit 0; default workflow discovery |
| Frontend install | `npm --prefix frontend ci --no-audit --fund=false` | Exit 0 |
| Frontend audit | `npm --prefix frontend audit --audit-level=moderate` | Exit 0; 0 vulnerabilities |
| Frontend typecheck | `npm --prefix frontend run typecheck` | Exit 0 |
| Frontend tests | `npm --prefix frontend test` | Exit 0; 3 passed |
| Frontend build | `npm --prefix frontend run build` | Exit 0 |
| Compose configuration | Compose `config` for base+development and base+GPU; base and development example env files also checked with `config --format json` | Exit 0; API database URL password matches the configured Postgres password without shell overrides; no services started |
| Dockerfile syntax | `docker buildx build --check --file services/api/Dockerfile .`; same command for worker and ComfyUI Dockerfiles | All exit 0; no images or services started |
| Source secret scan | `go run github.com/zricethezav/gitleaks/v8@v8.30.1 dir <path> --redact --no-banner` on source, docs, and config paths | Each targeted scan exited 0; no leaks found |
| Container configuration scan | `docker run --rm --mount type=bind,source=<repo>,target=/src,readonly aquasec/trivy:0.76.2 config --exit-code 1 --severity HIGH,CRITICAL /src` | Exit 1; API and worker clean; ComfyUI Dockerfile has HIGH `DS-0002` because it runs as root |

The Python suite emitted one Starlette `TestClient` deprecation warning and skipped
one FFmpeg/FFprobe integration case because those executables are unavailable. The
frontend install also warned that ERE's npm policy blocked the esbuild postinstall
script; typecheck, tests, and production build nevertheless passed. Neither warning
was suppressed.

An unfiltered local `gitleaks dir .` invocation exited 1 with 206 matches, all inside
the ignored local `.venv` third-party license database. A targeted scan of source,
documentation, workflow, and configuration paths found no leaks. The clean-checkout
GitHub scan has not yet run. The ComfyUI container finding was not suppressed or
changed without validating mounted model/input/output permissions. Issue
[#16](https://github.com/ofayese/OJU-Video-Forge/issues/16) tracks the rootless
container fix, and the optional GPU feature issue #10 depends on it. The Trivy
configuration job will remain failing until the finding is fixed.

## Not yet verified

- GitHub Actions results, including CodeQL, Gitleaks on a clean checkout, Trivy
  image scans, and Syft SBOM generation.
- Actual API/worker image builds and vulnerability results.
- Main-branch required checks or protection; owner authorization is needed before
  changing repository rules.
- Full authenticated upload-to-render-to-authorized-retrieval workflow, live
  database migration, GPU inference, and public deployment.
- Governance pull request publication and final merge readiness.

ADR-OVF-001 and ADR-OVF-002 remain **PROPOSED**.
