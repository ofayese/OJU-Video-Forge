# Evidence classification and sanitization

## Public

Publish only after review: test summaries, exact software versions, synthetic
fixtures, source, public dependency manifests, architecture decisions without
sensitive details, sanitized logs, and non-sensitive acceptance evidence.

## Restricted — never commit

- Access tokens, passwords, private keys, provider credentials, cookies, or session
  material.
- Real user media, private prompts, generated private artifacts, and personal data.
- Database backups/dumps, internal network and service inventories, private host
  identifiers, operational logs with sensitive values, and unpublished security
  findings.

## Sanitization checklist

- [ ] Remove credentials, tokens, private keys, authorization headers, and signed URLs.
- [ ] Replace real media, names, prompts, and identifiers with synthetic values.
- [ ] Remove internal hostnames, network names, IPs, ports, topology, storage paths,
  service inventories, and detailed machine inventories unless essential and
  explicitly approved for publication.
- [ ] Redact request bodies, database rows, stack traces with environment values,
  and log metadata that could identify users or systems.
- [ ] Preserve the test outcome and limits while replacing raw output with a short
  summary.
- [ ] Check filenames, metadata, screenshots, archives, and generated SBOMs.
- [ ] Run secret scanning and inspect the staged diff before commit.
- [ ] Keep source commit, command, environment class, exit code, date, and remaining
  gaps so evidence remains reproducible without exposing restricted raw data.

Git ignore rules are hygiene only; they do not prevent already tracked files or
historical objects from being public.
