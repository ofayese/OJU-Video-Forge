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

## ComfyUI non-root follow-up (sandbox, 2026-11-06)

This follow-up was run in a Linux amd64 sandbox, not ERE, with Docker Engine
28.0.4, Compose 2.38.2, Python 3.12.3, and Trivy 0.76.2. It does not replace
the historical ERE results above.

| Command | Result |
|---|---|
| `docker build -f services/comfyui/Dockerfile -t oju-comfyui:nonroot .` | Exit 0; image `sha256:199dfce48cd4c5cbc1a5fbde2d7b6ae18ba02dcec9f16be4a0baa1b790efd255`, upstream ComfyUI commit `63e5bf594339f676b4b15e072529873e3448409e` |
| `docker buildx build --check --file services/comfyui/Dockerfile .` | Exit 0; no warnings |
| `docker compose -f compose.yaml -f compose.gpu.yaml --profile gpu config -q` | Exit 0; configuration only |
| `docker run --rm --mount type=bind,source="$PWD",target=/src,readonly aquasec/trivy:0.76.2 config --exit-code 1 --severity HIGH,CRITICAL /src/services/comfyui/Dockerfile` | Exit 0; no HIGH/CRITICAL misconfigurations. Download of updated checks failed due to DNS; embedded checks were used. |
| `docker run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy:0.76.2 image --exit-code 1 --severity HIGH,CRITICAL oju-comfyui:nonroot` | Exit 1; vulnerability database download failed due to DNS. Image vulnerabilities remain unverified. |
| `/tmp/ovf-comfy-venv/bin/python -m pytest -q tests` | Exit 0; 70 passed, 1 skipped, 1 Starlette TestClient deprecation warning. The venv used `pip install --require-hashes -r requirements-dev.lock.txt`. |
| `/tmp/ovf-comfy-venv/bin/ruff check tests/test_comfyui_container.py` | Exit 0 |
| `docker run --rm --cap-drop ALL --security-opt no-new-privileges oju-comfyui:nonroot python main.py --cpu --quick-test-for-ci --temp-directory /home/forge --previews-directory /home/forge/previews` | Exit 1; upstream `comfy-kitchen` 0.2.41 uses a type unsupported by PyTorch 2.6.0's custom-op schema. Startup and inference remain unverified; Gate D still requires compatible, pinned versions. |

The following synthetic mount test exited 0, reporting UID/GID `10001:10001`,
`CapEff: 0000000000000000`, and `mount-check-passed`. It uses only temporary
files and does not change existing model or media permissions:

```bash
set -eu
D=$(mktemp -d /tmp/ovf-comfy-test.XXXXXX)
chmod 755 "$D"
mkdir "$D/models" "$D/input" "$D/output"
printf 'synthetic-model\n' > "$D/models/model.txt"
docker run --rm --user 0:0 --entrypoint sh --mount "type=bind,source=$D,target=/mnt" oju-comfyui:nonroot -c 'chown 10001:10001 /mnt/models /mnt/input /mnt/output && chmod 750 /mnt/models /mnt/input /mnt/output'
docker run --rm --cap-drop ALL --security-opt no-new-privileges --entrypoint sh --mount "type=bind,source=$D/models,target=/opt/ComfyUI/models" --mount "type=bind,source=$D/input,target=/opt/ComfyUI/input" --mount "type=bind,source=$D/output,target=/opt/ComfyUI/output" oju-comfyui:nonroot -c 'set -eu; id; test "$(id -u)" = 10001; test "$(id -g)" = 10001; grep CapEff /proc/self/status; test "$(cat models/model.txt)" = synthetic-model; echo synthetic-input > input/test.txt; echo synthetic-output > output/test.txt; mkdir -p /home/forge/temp /home/forge/previews; echo mount-check-passed'
docker run --rm --user 0:0 --entrypoint sh --mount "type=bind,source=$D,target=/mnt" oju-comfyui:nonroot -c 'rm -rf /mnt/models /mnt/input /mnt/output'
rmdir "$D"
```

No GPU generation was attempted. Neither the successful build, Compose check,
nor mount test demonstrates GPU or model support.
