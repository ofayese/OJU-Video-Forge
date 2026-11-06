# Branching strategy

- `main` is the integration branch. Do not push directly.
- Use short-lived branches: `feature/<issue-or-scope>`, `fix/<issue-or-scope>`,
  `security/<issue-or-scope>`, or `chore/<issue-or-scope>`.
- Start from a fetched, current `main`; confirm the worktree and preserve uncommitted
  changes before switching branches.
- Keep each PR focused. Link an issue and keep prerequisite/dependency order explicit.
- Use Conventional Commit subjects where practical, for example
  `feat(api): add project membership checks`.
- Do not force-push, delete protected branches, rewrite public history, or merge
  without explicit authorization. Do not auto-merge.
- A GitHub branch rule is not currently configured. Required checks must not be
  enabled until workflows have run successfully and maintainers have selected the
  final check names. See the [Definition of Done](./DEFINITION-OF-DONE.md).
