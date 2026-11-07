# Development workflow

OJU Video Forge is developed and validated locally on ERE. Cloud services are
optional; no Azure account, AD domain join, or public deployment is needed.
`ADR-OVF-001` and `ADR-OVF-002` remain proposed; see the [ADR index](../governance/ADR-INDEX.md).

## Setup

Use Python 3.12, Node.js 26 (or the project version declared by CI), npm, and Docker
Compose v2. Create a private local `.env` from `.env.development.example`; never
commit it. Keep media, model, database, and generated artifact data out of Git.

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-dev.lock.txt
npm --prefix frontend ci
```

Linux/WSL:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock.txt
npm --prefix frontend ci
```

If dependency inputs change, regenerate both Python locks using the pinned
`pip-tools` from the development requirements. `--allow-unsafe` is required so
pip and setuptools are pinned and hash-checked for clean `--require-hashes`
installs:

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m piptools compile --generate-hashes --allow-unsafe --output-file requirements.lock.txt requirements.txt
.\.venv\Scripts\python.exe -m piptools compile --generate-hashes --allow-unsafe --output-file requirements-dev.lock.txt requirements-dev.txt

## Local checks

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\pip-audit.exe -r requirements.lock.txt
.\.venv\Scripts\pip-audit.exe -r requirements-dev.lock.txt
.\.venv\Scripts\ruff.exe check app tests alembic scripts
.\.venv\Scripts\python.exe -m pytest -q
npm --prefix frontend audit
npm --prefix frontend run typecheck
npm --prefix frontend test
npm --prefix frontend run build
docker compose -f compose.yaml -f compose.dev.yaml config --quiet
docker compose -f compose.yaml -f compose.gpu.yaml config --quiet
```

Linux/WSL equivalents:

```sh
.venv/bin/python -m pip check
.venv/bin/pip-audit -r requirements.lock.txt
.venv/bin/pip-audit -r requirements-dev.lock.txt
.venv/bin/ruff check app tests alembic scripts
.venv/bin/python -m pytest -q
npm --prefix frontend audit
npm --prefix frontend run typecheck
npm --prefix frontend test
npm --prefix frontend run build
docker compose -f compose.yaml -f compose.dev.yaml config --quiet
docker compose -f compose.yaml -f compose.gpu.yaml config --quiet
```

These checks validate code/configuration; Compose `config` does not start services.
Use the [testing strategy](./TESTING-STRATEGY.md) before running tests that need
PostgreSQL, Redis, or FFmpeg. Do not touch unrelated local containers.

## Change workflow

1. Start from the latest `main` and create a focused branch.
2. Link work to a GitHub issue with acceptance criteria and dependencies.
3. Keep the diff small and preserve unrelated changes.
4. Run relevant checks and capture the exact command, runtime, exit code, warnings,
   skips, and limitations.
5. Update code, tests, docs, and acceptance evidence together.
6. Open a PR; wait for CI and review. Never merge automatically.

See [branching strategy](./BRANCHING-STRATEGY.md), [Definition of Done](./DEFINITION-OF-DONE.md),
and [agent execution policy](./AGENT-EXECUTION-POLICY.md).
