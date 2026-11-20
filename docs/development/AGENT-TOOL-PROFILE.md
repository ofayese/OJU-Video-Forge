# Tool profile for coding agents (Kilo Code / Copilot)

Companion to [`AGENTS.md`](../../AGENTS.md). This file covers **tool exposure**, not project
rules — project identity, evidence hierarchy, and security boundaries stay in
`AGENTS.md` and are not duplicated here.

## Instruction sources actually loaded

Verified against the installed tooling on 2026-11-20:

- **Kilo Code** loads the root `AGENTS.md` automatically and gives it project scope
  (priority 3, below the per-agent prompt and the project `instructions` key in
  `kilo.jsonc`). A project-level `kilo.jsonc` `instructions` entry can _override_
  `AGENTS.md`; do not use it to restate project rules — only to point at them.
  `.kilo/rules/` (and legacy `.kilocode/rules/`) hold Kilo-specific rules; current
  usage is `.kilo/.gitignore` only.
- **GitHub Copilot** loads `.github/copilot-instructions.md` (repository-wide) and
  `.github/instructions/*.instructions.md` (path-scoped, `applyTo`), and the root
  `AGENTS.md` as agent instructions for Copilot coding agent / agent mode. Those files
  reference `AGENTS.md` instead of copying it.

Do not assume every agent reads `AGENTS.md`. If a client does not, it still reads
`.github/copilot-instructions.md`, which points at the root file.

## Minimum useful tool set

Keep these enabled; they cover inspect → change → validate → evidence:

- File inspection: read, grep, glob, list-definitions (or the client's equivalents).
- Editing: targeted edit / write-file.
- PowerShell terminal (Windows 11, Windows PowerShell 5.1 — no `&&`, `||`, `?:`).
- Git (read-only inspection plus local commits).
- Python + pytest inside `.venv`.
- Node/npm for `frontend/` (typecheck, vitest, vite build).
- Docker / Docker Compose (config validation, image inspection, logs).

## Tool-exposure limit (128 tools)

Some clients expose a hard cap on tool definitions. When the cap is hit, tool
providers get truncated in an order the model does not control, and essential tools
(edit, terminal, git) can disappear mid-session. Mitigations:

1. Disable unrelated tool providers for this workspace before starting a task; keep
   only the minimum set above. Provider toggles live in client settings, not here.
2. Do not add tool-heavy MCP servers or skills for a task that does not need them.
3. If a tool is missing, do not invent a call. Use the tools actually exposed
   (read the file with the plain read tool instead of a ranged/`view_range` variant,
   run one shell command instead of a batched multi-tool step) and note the
   substitution in the execution report.

Instructions alone cannot raise the cap; the fix is a smaller active tool profile.

## Environment discovery before validating

Run these once per session and quote the output in your report:

- `git rev-parse HEAD; git rev-parse --abbrev-ref HEAD; git status --short`
- `.\.venv\Scripts\Activate.ps1` then `python -c "import sqlalchemy, fastapi, rq, redis"`
- `docker version --format '{{.Server.Version}}'`
- `docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}'`

The running containers do not prove the image was built from the current commit; the
image labels carry no revision on this host, so compare image creation time to the
commit time before making any runtime-acceptance claim.
