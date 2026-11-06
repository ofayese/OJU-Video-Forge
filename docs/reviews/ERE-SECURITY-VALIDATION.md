# ERE Security Validation

**Status:** PARTIAL. The nine original Dependabot alerts and the FastAPI/Starlette follow-up are merged to `main`. PR [#4](https://github.com/ofayese/OJU-Video-Forge/pull/4) merged as `8e462ec233c3cecc1fcf225a0f02e0d551ef50f8`. This is not a full application security certification.

## Remediation status

| Scope | Versions | Result |
|---|---|---|
| Original Dependabot remediation | `python-multipart` `0.0.20` → `0.0.31`; Vitest `^2.1.8` → `^4.1.11` (lockfile resolves `4.1.12`) | PR [#3](https://github.com/ofayese/OJU-Video-Forge/pull/3), head `c4b3e9217abb37c5ceaf037c2d7f95d59568354e`, merged as `486e387931dd7c8b022d14348f5f9ec6749f432c`. GitHub reports 9 fixed alerts and 0 open alerts. Validation used Node `26.12.1`, Vite `6.4.5`, React `19.3.1`, and TypeScript `5.9.3`. |
| FastAPI/Starlette follow-up | FastAPI `0.115.12` → `0.162.0`; Starlette `0.46.2` → `1.10.0` | PR [#4](https://github.com/ofayese/OJU-Video-Forge/pull/4), branch `security/fastapi-starlette-hardening-2026-11`, head `1409fc45eceba6602649a5a6fbe1542ee18a46d8`; merged as `8e462ec233c3cecc1fcf225a0f02e0d551ef50f8`. |

FastAPI `0.162.0` metadata requires Starlette `>=0.46.0` and Pydantic `>=2.9.0`; the repository's Pydantic `2.11.3` satisfies that constraint. Starlette `1.10.0` requires Python `>=3.11`; ERE and the API container use Python 3.12. The effective Python minimum is therefore 3.11. No Python 3.10 support was declared by the repository.

FastAPI `0.162.0` changes omitted boolean form-field defaults; the application has no boolean form fields. Starlette `1.10.0` changes equality ordering for repeated multidict values and deprecates `FormMessage`; neither behavior is used by the application.

The follow-up resolves the seven Starlette advisories previously reported by `pip-audit`:

| pip-audit ID | CVE / GHSA | Advisory's first patched version |
|---|---|---|
| `PYSEC-2026-1941` | CVE-2025-54121 / [GHSA-2c2j-9gv5-cj73](https://github.com/advisories/GHSA-2c2j-9gv5-cj73) | `0.47.2` |
| `PYSEC-2026-1942` | CVE-2025-62727 / [GHSA-7f5h-v6xp-fcq8](https://github.com/advisories/GHSA-7f5h-v6xp-fcq8) | `0.49.1` |
| `PYSEC-2026-161` | CVE-2026-48710 / [GHSA-86qp-5c8j-p5mr](https://github.com/advisories/GHSA-86qp-5c8j-p5mr) | `1.0.1` |
| `PYSEC-2026-2281` | CVE-2026-48818 / [GHSA-wqp7-x3pw-xc5r](https://github.com/advisories/GHSA-wqp7-x3pw-xc5r) | `1.1.0` |
| `PYSEC-2026-2280` | CVE-2026-48817 / [GHSA-x746-7m8f-x49c](https://github.com/advisories/GHSA-x746-7m8f-x49c) | `1.1.0` |
| `PYSEC-2026-249` | CVE-2026-54283 / [GHSA-82w8-qh3p-5jfq](https://github.com/advisories/GHSA-82w8-qh3p-5jfq) | `1.3.1` |
| `PYSEC-2026-248` | CVE-2026-54282 / [GHSA-jp82-jpqv-5vv3](https://github.com/advisories/GHSA-jp82-jpqv-5vv3) | `1.3.0` |

The selected Starlette `1.10.0` is above all listed patch levels. The GitHub Advisory Database check found no vulnerabilities for FastAPI `0.162.0`, Starlette `1.10.0`, or `python-multipart 0.0.31`; `pip-audit` independently found no known vulnerabilities in the current Python requirements.

## Validation evidence

All commands ran on ERE (Windows 11 host or its local Linux Docker engine); no application was deployed publicly and no database migration was executed.

| Command / check | Exit | Result |
|---|---:|---|
| Install `requirements.txt` in an isolated Python 3.12 environment | 0 | Resolved FastAPI `0.162.0`, Starlette `1.10.0`, and `python-multipart 0.0.31`. |
| `python -m pip check` | 0 | No broken requirements. |
| `python -m pip-audit -r requirements.txt` | 0 | No known vulnerabilities. |
| `python -m pytest -q -p no:cacheprovider` (ERE Windows) | 0 | 63 passed, 1 skipped; 1 Starlette `TestClient` deprecation warning. |
| Same pytest command in Python 3.12 Linux container on ERE | 0 | 63 passed, 1 skipped; same deprecation warning. |
| `npm --prefix frontend audit` | 0 | 0 vulnerabilities. |
| `npm --prefix frontend run typecheck` | 0 | Passed on the original remediation branch. |
| `npm --prefix frontend test` | 0 | 3 passed on the original remediation branch. |
| `npm --prefix frontend run build` | 0 | Production build passed on the original remediation branch. |
| FastAPI/Uvicorn loopback startup and `GET /health/live` | 0 | HTTP 200, `{"ok":true}`. |
| Focused API tests | 0 | Startup/liveness, authentication (401/valid bearer), viewer role denial (403), and OpenAPI operation generation passed. |
| Multipart upload test | 0 | FastAPI/Starlette accepted an uploaded file using `python-multipart 0.0.31`. |
| `python -m alembic upgrade head --sql` | 0 | Offline SQL generated with the expected tables; no database was changed. |
| `python -c "import app.worker"` | 0 | Render-worker imports with the updated requirements. |
| GitHub Dependabot query | 0 | 9 fixed alerts, 0 open alerts for the original remediation. |

The Windows newline assertion was corrected to emit no line ending, preserving the subprocess security assertions (`shell=False`, bounded output, timeout, and safe stdin/stderr handling). Before that correction, the Windows suite had failed only because it expected LF while Python emitted CRLF.

The skipped test is the real FFmpeg/FFprobe rendering fixture because those executables are unavailable in the test environment. Starlette emits a `DeprecationWarning` because its `TestClient` currently uses the repository's pinned `httpx`; tests pass, but a future test-client dependency update may be needed.

## Pull request status

GitHub reports PR #4 as merged on 2026-11-06T03:59:03Z. Its code and evidence commits are preserved on `main`.

## Remaining release-readiness limits

Dependency scans and targeted compatibility checks pass, but overall application release readiness remains **NOT VERIFIED**. The Alembic migration was checked offline only; there was no live database migration or database integration test. GPU/ComfyUI inference and end-to-end rendering were not exercised. Broader authorization, isolation, recovery, container-image scanning, and deployment checks remain outside this remediation.
