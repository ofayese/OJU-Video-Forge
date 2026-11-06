# Azure Readiness — Future Extension Points

**Status:** Documentation only. No Azure resource or credential is required for local development; none was provisioned.

The application should retain provider interfaces so a future, separately approved project can evaluate:

| Concern | Local-first implementation | Possible future provider |
|---|---|---|
| Identity | Local account/session provider | Microsoft Entra ID after confirming issuer and discovery metadata |
| Relational data | Local PostgreSQL | Azure Database for PostgreSQL |
| Artifact storage | Private local storage | Azure Blob Storage |
| Secrets | Local development environment configuration | Azure Key Vault |
| Compute | Docker Compose and local workers | Azure Container Apps or another approved runtime |
| Observability | Local structured logs/health | Azure Monitor |

Do not infer an OIDC issuer from the AD domain. Keep database, Redis, ComfyUI, and workers private. Cloud extensions must not be required by the local development path.
