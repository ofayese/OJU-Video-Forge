# OJU Video Forge — Development Secrets and Credential Rotation Runbook

**Branch:** `feat/silent-recording-training`
**Applies to:** ERE development host only

---

## Secret Inventory

| Secret | Storage Location | Delivery Method | Git Status |
|---|---|---|---|
| DB_PASSWORD | `.env` (host) | Compose env var `${DB_PASSWORD:?}` | Gitignored |
| FORGE_TOKEN_DEV_USER | `.env` (host) | Compose env var | Gitignored |
| FORGE_TOKEN_DEV_REVIEWER | `.env` (host) | Compose env var | Gitignored |
| FORGE_TOKEN_DEV_ADMIN | `.env` (host) | Compose env var | Gitignored |
| FORGE_OPENWEBUI_TOKEN | `.env` (host) | Compose env var | Gitignored |
| FORGE_CHATGPT_TOKEN | `.env` (host) | Compose env var | Gitignored |
| HMAC signing key | `C:\ProgramData\OJU Video Forge\secrets\hmac.key` | File mount `/run/secrets/telemetry-hmac.key:ro` | Not in repo |
| GPG_KEY | Environment | Compose env var | Gitignored |

---

## Provisioning Dev Bearer Tokens

```
# From the repository root on ERE:
DB_PASSWORD=<password> python scripts/provision_dev_auth.py
```

- Reads `DATABASE_URL` or `DB_PASSWORD` from environment — no secret embedded in source
- Creates AuthSession records with SHA256-hashed tokens in PostgreSQL
- Writes plaintext tokens to `.env` (gitignored)
- Dev users authenticate exclusively via bearer tokens, not passwords
- Tokens expire after 30 days

**Never commit:** bearer tokens, `.env` contents, HMAC key, database backups.

---

## Token Rotation Process

1. Run `scripts/provision_dev_auth.py` again — it revokes existing sessions and creates new ones
2. Update `.env` with new tokens (script appends automatically)
3. Restart API/worker containers to pick up new `.env` values
4. Old tokens remain valid until expiry (30 days) — plan overlap window

**Do not** perform live credential rotation on running services without approval.

---

## HMAC Key Management

- Key file: `C:\ProgramData\OJU Video Forge\secrets\hmac.key`
- Mounted read-only into containers: `/run/secrets/telemetry-hmac.key:ro`
- Never delivered as an environment variable
- Stored outside the telemetry observation directory
- Consumers see only the single key file, never the publisher's key-storage directory

**To rotate:** Replace the key file, update the publisher, restart consumers. Old observations signed with the previous key will fail verification — plan key overlap.

---

## CI Secrets

- `.github/workflows/ci-backend.yml` uses CI-only placeholder values (`ci-only-not-a-secret`, `ci-test-token`)
- These are not production secrets and are safe in version control
