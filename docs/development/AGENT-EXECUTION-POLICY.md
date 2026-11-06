# Agent execution policy

Repository Copilot customizations are scoped helpers, not authorization grants.
Agents must follow `.github/copilot-instructions.md`, the issue/PR scope, and the
same approval boundaries as human contributors.

- Architecture review is read-only and recommends; it cannot approve ADRs.
- Implementation agents change only the approved task scope, preserve unrelated
  work, and do not deploy, migrate shared data, or access external infrastructure.
- Security review is read-only. Report sensitive vulnerabilities through private
  channels, not public issue text.
- Validation agents use local or isolated CI services, record actual evidence, and
  never change application behavior or suppress failures to create a PASS.
- No agent may obtain/modify credentials, bypass permission denials, publish private
  data, or merge a pull request.
- Confirm destructive or externally visible actions with the owner. Stop on denied
  authorization; do not retry through a different interface.
- Separate observed results from assumptions, proposals, and unverified gates.

See [VS Code custom agent documentation](https://code.visualstudio.com/docs/copilot/customization/custom-agents)
and [custom instruction documentation](https://code.visualstudio.com/docs/copilot/customization/custom-instructions).
