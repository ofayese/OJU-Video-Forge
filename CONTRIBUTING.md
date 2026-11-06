# Contributing

Thanks for helping improve OJU Video Forge. The project is local-first and
experimental; it is **not production ready**.

## Before you start

- Review [development workflow](docs/development/DEVELOPMENT-WORKFLOW.md),
  [branching strategy](docs/development/BRANCHING-STRATEGY.md),
  [Definition of Done](docs/development/DEFINITION-OF-DONE.md), and
  [public repository security](docs/governance/PUBLIC-REPOSITORY-SECURITY.md).
- Search GitHub Issues before creating work. Keep public issues free of secrets,
  private media/prompts, raw logs, and internal infrastructure details.
- Work from a focused issue and branch. Do not push directly to `main`.

## Development and tests

Follow the platform-specific setup in
[DEVELOPMENT-WORKFLOW.md](docs/development/DEVELOPMENT-WORKFLOW.md). Use committed
Python and npm lockfiles. Do not connect tests to shared databases or unrelated
local services.

Run the relevant checks from [TESTING-STRATEGY.md](docs/development/TESTING-STRATEGY.md)
and include exact commands, exit codes, warnings, skips, and limitations in the PR.
Do not claim CI success until the corresponding GitHub Actions run completes.

## Pull requests

Use the PR template. Link the issue, state security/data impact, update docs, and
record acceptance evidence. Prefer small PRs and Conventional Commit messages.
Resolve review conversations and wait for checks. Do not merge automatically.

Keep ADR-OVF-001 and ADR-OVF-002 **PROPOSED** unless an accountable owner approves
them after their validation gates.
