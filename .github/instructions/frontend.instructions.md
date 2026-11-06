---
name: Frontend Engineering
description: "Use for React, TypeScript, Vite, API client, frontend tests, and user-facing accessibility."
applyTo:
  - "frontend/**/*.ts"
  - "frontend/**/*.tsx"
---

# Frontend guidance

- Keep API types and calls in `frontend/src/api/`; keep server authorization
  authoritative and handle 401/403 responses without exposing private data.
- Use strict TypeScript and existing React/Vite patterns. Do not add mock behavior as
  a substitute for an unimplemented backend workflow.
- Keep loading, error, empty, and success states accessible and explicit.
- Use the committed `frontend/package-lock.json`; validate with `npm ci`,
  `npm run typecheck`, `npm test`, and `npm run build`.
- Do not store credentials, private media, prompts, or generated artifacts in browser
  storage or committed fixtures.
