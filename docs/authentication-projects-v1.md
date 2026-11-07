# Local authentication and project API v1

The `/v1` API supports local accounts and project-scoped authorization. It does not
require Active Directory, Entra ID, OIDC, or a cloud service. Existing configured
Open WebUI and ChatGPT service bearer tokens remain available for their existing
integration routes; they do not grant access to user projects.

## Local accounts and sessions

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/auth/register` | Create an account with a unique username and password |
| `POST` | `/v1/auth/session` | Exchange username/password for an opaque bearer session |
| `GET` | `/v1/auth/session` | Return the authenticated local user |
| `DELETE` | `/v1/auth/session` | Revoke the current session |

Usernames are normalized to lowercase and accept 3–64 letters, numbers, dots,
underscores, or hyphens. Passwords must contain 12–128 characters. Passwords are
stored as salted scrypt hashes; the random session token is returned only at login
and its SHA-256 digest is stored in the database. Sessions expire after 12 hours.
Send the returned token using the bearer scheme in the `Authorization` header on
protected requests.
Invalid, expired, and revoked credentials are rejected. API responses and errors
never include password or token digests.

Registration is enabled for local account bootstrap and is not an administrator
privilege. The API should remain on its configured private local/Compose network.

## Projects and roles

| Method | Path | Required access |
|---|---|---|
| `POST` | `/v1/projects` | Authenticated local user; creator becomes owner |
| `GET` | `/v1/projects` | Lists only projects with the caller's membership |
| `GET` | `/v1/projects/{project_id}` | Any project member |
| `PATCH` | `/v1/projects/{project_id}` | Project editor or owner |
| `GET` | `/v1/projects/{project_id}/members` | Any project member |
| `PUT` | `/v1/projects/{project_id}/members/{username}` | Owner; grant `viewer` or `editor` |
| `DELETE` | `/v1/projects/{project_id}/members/{username}` | Owner; project owner cannot be removed |

The project role hierarchy is `owner` > `editor` > `viewer`. Owners manage the
membership list; editors may update project metadata; all members may read project
metadata and member names/roles. User identity and membership are checked by the
backend on every project request. Unknown and cross-project reads return `404`;
known members without the required role receive `403`. Service bearer tokens cannot
create, enumerate, or access local-user projects.

## Schema and compatibility

The account/session/project routes are versioned under `/v1`. Apply Alembic revision
`0005` after `0004` to add local users, revocable sessions, and project memberships
to the existing project schema. Existing projects and job records are preserved.

The OpenAPI document is available at `/openapi.json`. Generate it with:

```sh
python scripts/export_openapi.py --output /tmp/ovf-openapi.json
```
