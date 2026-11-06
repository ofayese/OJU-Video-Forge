# OJU Video Forge project instructions

## Domain and URL naming

- Use `ojuidc.olutech.systems` only for Active Directory identities and internal
  infrastructure. Do not use it as the public application hostname.
- Use `video.olutechsys.com` as the proposed Video Forge web application URL.
- Use `video-api.olutechsys.com` as the proposed API and external-integration URL.
- These public URLs are targets only. Do not claim that DNS, TLS, reverse proxy, or
  external access is configured unless it has been tested.
- Local development remains primary: use localhost or Docker service DNS names
  (such as `http://api:8000`) where appropriate.
- Never infer an OIDC issuer from the AD domain name. Use AD/OIDC only as an optional
  integration after confirming the provider's actual issuer and discovery metadata.
- Keep database, Redis, ComfyUI, and worker services private; do not make them public
  to satisfy application URL requirements.

## Architecture status and claims

- Keep `ADR-OVF-001.md` in **PROPOSED** status until validation gates pass and an
  accountable owner explicitly approves it.
- Do not mark GPU support, migrations, deployment, tests, or integrations verified
  unless the relevant checks have actually completed successfully.
- Preserve the local-first requirement; no Azure subscription or cloud service may be
  required for the local development path.
