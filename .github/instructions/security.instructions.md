---
name: Security Engineering
description: "Use when changing authentication, authorization, uploads, media, artifacts, dependencies, subprocesses, containers, or workflows."
applyTo: "app/**/*.py, services/**, compose*.yaml, requirements*.txt, frontend/package*.json, .github/workflows/**"
---

# Security guidance

- Enforce identity, role, project ownership, and artifact access in trusted backend
  code. Default to deny when identity or ownership cannot be established.
- Keep secrets out of source, logs, workflow output, public issues, and examples.
  Example values must be unmistakable placeholders, never usable credentials.
- Use safe subprocess APIs and explicit resource limits. Do not pass user input to a
  shell, executable path, workflow node mapping, or unrestricted FFmpeg option.
- Store media and artifacts in private roots; use IDs rather than user paths for
  retrieval and verify ownership on every access.
- Keep services private, use least privilege, pin dependencies/actions where
  practical, and scan manifests and lockfiles.
- Do not disclose exploit details publicly before coordinated remediation. Report
  sensitive findings through the repository's private reporting route.
- Do not downgrade controls, silence scanners, or suppress tests to make CI pass.
