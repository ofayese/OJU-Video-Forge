---
name: Backend Engineering
description: "Use for FastAPI, Python, SQLAlchemy, Alembic, Redis, RQ, API contracts, and backend configuration."
applyTo: "app/**/*.py, alembic/**/*.py"
---

# Backend guidance

- Follow the existing `app/` package, Pydantic settings, SQLAlchemy 2 model, and
  FastAPI dependency patterns. Keep endpoint contracts in `/v1`.
- Validate request size and fields; enforce actor roles and project ownership in the
  API/service layer, including reads and artifact access.
- Use migrations for schema changes. Never create or apply a migration against a
  shared or production database as part of local validation.
- Persist the authoritative job state before queue dispatch. Make failed dispatch,
  retry, duplicate delivery, cancellation, and recovery visible and testable.
- Keep database and Redis access behind private local/Compose service boundaries.
- Do not report a framework or integration as supported based only on package
  resolution or application import.
