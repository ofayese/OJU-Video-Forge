# Dependency update policy

- `requirements.txt` is the runtime input; `requirements.lock.txt` is the
  hash-checked transitive resolution used by Docker and runtime installations.
- `requirements-dev.txt` is the developer/CI input; `requirements-dev.lock.txt`
  pins the full development and runtime graph. Use the pinned `pip-tools` version
  to regenerate locks and commit them with manifest changes.
- `frontend/package.json` declares frontend dependencies and
  `frontend/package-lock.json` is authoritative. Use `npm ci` in CI.
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
