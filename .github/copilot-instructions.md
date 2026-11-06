# OJU Video Forge engineering instructions

These are project-wide requirements. Consult the linked architecture decisions and
development policies for detail; do not infer deployment, security, or acceptance
status from configuration alone.

## Architecture and operating boundary

- Keep development, tests, and the supported local workflow local-first. Azure and
  other cloud services are optional adapters, never prerequisites.
- ERE is the current development and validation host. Do not migrate, deploy, or
  access ARABA, AKOLE, or other infrastructure without separate explicit approval.
- Local authentication must not depend on Active Directory, Entra ID, or an external
  OIDC provider. Never infer an issuer; verify discovery metadata before optional
  federation.
- Keep database, Redis, worker, ComfyUI, models, media, and artifacts on private
  local or Compose networks. Do not expose them to satisfy public URL requirements.
- Preserve `ADR-OVF-001.md` and `ADR-OVF-002.md` as **PROPOSED** until validation
  gates pass and an accountable owner explicitly approves them.
- Treat the proposed application domains and infrastructure values as design targets,
  not evidence that DNS, TLS, routing, or external access exists.

## Security and application behavior

- Enforce authentication, role checks, project ownership, and artifact authorization
  on the backend; frontend visibility is not an authorization control.
- Treat uploaded media, timeline JSON, prompts, filenames, and generated outputs as
  untrusted. Validate size, type, paths, ownership, retention, and retrieval scope.
- Run media tools with fixed, reviewed argument arrays, bounded time/resources, no
  shell, and private storage. Do not accept user-supplied executable paths or flags.
- Persist job state before dispatch. Make retries, duplicate delivery, cancellation,
  worker failure, and recovery explicit and auditable.
- Schedule GPU work against available resources and configured concurrency; do not
  assume GPU support from a Compose profile or driver inventory.
- Record provenance for jobs and artifacts, including the immutable input identity,
  render/model configuration, tool versions, and output checksum where applicable.

## Engineering and validation

- Deliver working, reviewable increments. Keep API contracts and persisted workflow
  formats versioned; update consumers and documentation together.
- Establish reproducible dependency resolution and use the repository's lockfiles.
- Make deterministic CPU/FFmpeg rendering operational before prioritizing GPU
  inference. GPU generation is an optional later capability.
- Implement real application behavior before claiming a feature is complete. A stub,
  prompt submission, mock, configuration check, or generated schema is not an
  end-to-end feature.
- Run relevant tests and scans and report exact commands, results, environment, and
  limitations. Never claim an unexecuted check passed or weaken a failing test to
  obtain a green result.
- Do not commit credentials, private prompts/media, raw host inventories, backups,
  sensitive logs, or generated private artifacts. Review evidence before publishing.
- Avoid modifying unrelated services, data, models, or infrastructure.
- Use focused branches and pull requests; do not push directly to `main`, rewrite
  published history, or merge without explicit owner authorization.

## Project references

- Architecture and boundaries: [ADR-OVF-001](../ADR-OVF-001.md),
  [ADR-OVF-002](../ADR-OVF-002.md), and [ADR index](../docs/governance/ADR-INDEX.md).
- Development and quality gates: [development workflow](../docs/development/DEVELOPMENT-WORKFLOW.md),
  [Definition of Done](../docs/development/DEFINITION-OF-DONE.md), and
  [testing strategy](../docs/development/TESTING-STRATEGY.md).
- Security and evidence handling: [public-repository security](../docs/governance/PUBLIC-REPOSITORY-SECURITY.md),
  [evidence classification](../docs/governance/EVIDENCE-CLASSIFICATION.md), and
  [evidence retention](../docs/governance/EVIDENCE-RETENTION.md).
- First integrated application milestone: [vertical-slice plan](../docs/implementation/VERTICAL-SLICE-PLAN.md).
