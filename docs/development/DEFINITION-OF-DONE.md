# Definition of Done

A change is complete only when all applicable items are satisfied:

- [ ] The issue's observable behavior and acceptance criteria are implemented.
- [ ] API, database, worker, and frontend contracts agree and are versioned.
- [ ] Authorization and ownership are enforced server-side for every affected path.
- [ ] Media, prompts, artifacts, errors, and logs follow the privacy policy.
- [ ] Relevant tests pass; skips, warnings, failures, and unavailable checks are
  reported without suppression.
- [ ] Dependency resolution, scans, and compatibility are validated when affected.
- [ ] Documentation and operational guidance match actual behavior.
- [ ] No unexpected data loss, shared-service changes, migration, or deployment
  occurred.
- [ ] Evidence includes command, environment, exit code, validation date, source
  commit, and remaining gaps.
- [ ] CI is green and review conversations are resolved before merge.
- [ ] Rollback and recovery are understood for schema, storage, and job changes.

Governance PRs may be complete while application, GPU, or release-readiness gates
remain incomplete. See the [acceptance register](../reviews/ERE-ACCEPTANCE-REGISTER.yaml).
