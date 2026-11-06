# Public repository security

This repository is public. Treat every committed issue, workflow log, artifact, and
document as visible outside the project.

## Reporting

- Do not file exploitable vulnerability details, credentials, private media,
  sensitive logs, or private infrastructure facts in public issues.
- Use the repository's private vulnerability reporting feature when available. If it
  is unavailable, contact maintainers through a private GitHub channel and include
  only enough detail to coordinate a fix.
- The repository currently has GitHub secret scanning and push protection enabled;
  CI also scans changes. Automated scans do not replace review.

## Required controls

- Use least-privilege workflow permissions and immutable action commit references.
- Never use `pull_request_target` to execute untrusted pull request code.
- Use isolated, disposable service containers for tests; do not provide production
  credentials to CI.
- Keep application data, database/Redis, workers, models, and artifacts private.
- Review dependency, container, and source findings before merge. Record false
  positives with evidence rather than disabling the scanner.
- Keep local authentication independent of external identity and cloud services.

## Public release review

Before publishing code or evidence, run the checklist in
[Evidence Classification](./EVIDENCE-CLASSIFICATION.md). Report accidental secret
exposure promptly; push protection is preventive, not proof that history is clean.
Do not rewrite public history without separate explicit owner authorization.
