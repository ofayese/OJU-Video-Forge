# ADR-OVF-002: Hosting and Organizational Boundaries

- **Status:** PROPOSED
- **Date:** 2026-11-05
- **Decision owners:** Not assigned

## Context

OJU Video Forge is being developed for a local-first environment. The current workspace host reported hostname `ere`, Windows 11 Enterprise, and workgroup `WORKGROUP`; these observations are consistent with a standalone, non-domain-joined machine, but do not independently establish its organizational assignment. See [the ERE environment inventory](docs/operations/ere-environment-inventory.md).

Future OJUIDC infrastructure is a separate project. Public application URL targets do not establish that DNS, TLS, a reverse proxy, or external access are configured.

## Proposed decision

1. Keep local development and operation independent of Azure, Active Directory, Entra ID, and external OIDC. Enterprise identity may be an optional provider only after its actual issuer and discovery metadata are verified.
2. Treat `ojuidc.olutech.systems` as an internal identity/infrastructure namespace, not the public Video Forge application host.
3. Use `video.olutechsys.com` and `video-api.olutechsys.com` only as proposed future public application/API targets. Do not claim they resolve or are externally reachable without testing.
4. Keep PostgreSQL, Redis, ComfyUI, and workers on private local service networks. Do not expose their administrative interfaces to satisfy application URL requirements.
5. Do not connect to, deploy to, migrate to, or transfer project data to ARABA or any other OJUIDC infrastructure as part of this implementation.
6. Preserve local models, media, containers, volumes, and unrelated services. Do not join the host to a domain or configure cross-organizational trust.

## Consequences

- Local development uses localhost or private Compose service DNS names.
- Hosting-provider adapters must not make cloud services mandatory.
- Public DNS, TLS, reverse-proxy routes, and external integrations require a separate validated and approved change.
- Migration-readiness and deployment to ARABA remain out of scope.

## Validation and approval

This ADR remains **PROPOSED** until the applicable validation gates pass and an accountable owner explicitly approves it. The environment inventory records observations, not proof of organizational ownership or deployment readiness.
