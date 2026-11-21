# Dependency update policy

- `requirements.txt` is the runtime input; `requirements.lock.txt` is the
  hash-checked transitive resolution used by Docker and runtime installations.
- `requirements-dev.txt` is the developer/CI input; `requirements-dev.lock.txt`
  pins the full development and runtime graph. Use the pinned `pip-tools` version
  to regenerate locks and commit them with manifest changes.
- `frontend/package.json` declares frontend dependencies and
  `frontend/package-lock.json` is authoritative. Use `npm ci` in CI.
- `scripts/check_dependency_locks.py` must run with the `pip-tools` version that
  `requirements-dev.txt` pins. `pip-tools` imports `pip._internal`, so a newer pip
  that drops a private helper breaks an older `pip-tools`: `piptools.sync` on 7.5.0
  does `from pip._internal.utils.compat import stdlib_pkgs`, and pip 26.3 no longer
  exports it. If the checker fails with
  `ImportError: cannot import name 'stdlib_pkgs'`, it is running against a stale
  `pip-tools`, not a stale lock - do not regenerate locks to "fix" it. Repair the
  validation environment instead (leave the shared project environment alone):
  `python -m venv .venv-lockcheck` then
  `.venv-lockcheck\Scripts\python.exe -m pip install "pip==26.3" "pip-tools==7.7.0" "setuptools==84.2.1"`,
  and run the checker with `.venv-lockcheck\Scripts\python.exe`. A passing run
  prints `PASS: committed Python locks match their manifests`.
- Dependabot groups routine patch/minor version updates. Security updates remain
  individually reviewable and are triaged promptly.
- Patch/minor updates still require full relevant tests, `pip check`, audits, and
  lockfile regeneration. Review transitive changes and changelogs.
- Major upgrades are separate issues/PRs with compatibility analysis, migration
  notes, API/behavior tests, and explicit owner review. Never upgrade a major
  framework solely to satisfy a generic update recommendation.
- Vulnerability fixes take priority over routine churn. Confirm the resolved lock
  graph and scan result; a manifest edit alone does not close an advisory.
- If an update cannot be taken, document affected versions, impact, owner, mitigation,
  and review date. Do not disable audits or silently suppress alerts.
- License inventory is generated from the dependency graph in CI. Review new
  licenses before merge; do not claim legal approval from an automated scanner.
