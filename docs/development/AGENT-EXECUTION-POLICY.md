# Agent execution policy

Repository Copilot customizations are scoped helpers, not authorization grants.
Agents must follow [the root `AGENTS.md`](../../AGENTS.md) (canonical: project
identity, evidence hierarchy, module navigation, security boundaries),
`.github/copilot-instructions.md`, the issue/PR scope, and the same approval
boundaries as human contributors.

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

## Application agent boundary

The application agent adapter is disabled by default. The experimental local Ollama
adapter requires `AGENTS_ENABLED=true`, `AGENT_PROVIDER=ollama`, `AGENT_URL`,
`AGENT_MODEL`, and the approved model's 64-character lowercase SHA-256
`AGENT_MODEL_DIGEST`. These settings do not install a server, authorize a model,
or make a host endpoint reachable from the worker's private networks. Configure
only an explicitly approved OJU-owned endpoint and model; loopback refers to the
container itself. Do not reuse another workload's model service without approval.

The adapter checks model identity and completion capability before and after each
request, but this is not an atomic digest binding and does not establish semantic
correctness or production readiness. It supports structured training drafts and
the existing deterministic `render_test` proposal. Proposals are project-scoped,
validated, stored without the task prompt, and require a distinct project owner or
reviewer to approve before job dispatch. Agent service identities cannot approve;
arbitrary tools, shell commands, media access, and direct job execution are not
exposed. Model content is untrusted and requires human review.
Each HTTP operation has a 15-second inactivity timeout and a 90-second deadline
checked between response chunks; an in-progress read can exceed that deadline by
up to the read timeout. Cancellation is checked between chunks and requests.

The project-scoped training-agent status check reports disabled, unconfigured,
unavailable, degraded, or ready. Ready requires both a currently matching provider
and a previously schema-validated generation for that narration with the same
endpoint and model identity; it is not an inference test or a guarantee of
correctness, capacity, or availability on the next request. The network probe
runs from the API container and does not prove that the worker can reach the
provider; its actual connectivity is checked during draft generation.
