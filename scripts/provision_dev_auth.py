#!/usr/bin/env python3
"""Provision secure development-only bearer tokens for OJU Video Forge.

Creates AuthSession records in PostgreSQL with SHA256-hashed tokens,
and writes plaintext tokens to .env (gitignored). No credentials are
committed to Git.

Usage:
    DB_PASSWORD=<password> python scripts/provision_dev_auth.py

The script reads DB_PASSWORD from the environment (or DATABASE_URL) and
derives the connection from project configuration so no secret is
embedded in source.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg


def database_url() -> str:
    """Resolve the PostgreSQL connection URL from environment.

    Prefers DATABASE_URL; otherwise builds it from DB_PASSWORD and the
    standard OJU development service names.
    """
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    password = os.environ.get("DB_PASSWORD")
    if not password:
        print("ERROR: Set DATABASE_URL or DB_PASSWORD before running.", file=sys.stderr)
        sys.exit(1)
    return (
        f"postgresql+psycopg://forge:{password}@postgres:5432/forge"
    )


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def main() -> None:
    tokens: dict[str, str] = {}
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=30)

    url = database_url().replace("+psycopg", "")
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            # Ensure development users exist
            users = {
                "dev-user": False,
                "dev-reviewer": False,
                "dev-admin": True,
            }
            for username, is_admin in users.items():
                cur.execute(
                    "SELECT id FROM users WHERE username = %s",
                    (username,),
                )
                row = cur.fetchone()
                if row:
                    user_id = row[0]
                    cur.execute(
                        "UPDATE users SET disabled = false, administrator = %s WHERE id = %s",
                        (is_admin, user_id),
                    )
                else:
                    # Placeholder password hash: development users authenticate
                    # exclusively via bearer token sessions, not passwords.
                    password_hash = (
                        "scrypt$16384$8$1$dev_salt_base64==$dev_hash_base64=="
                    )
                    user_id = str(uuid4())
                    cur.execute(
                        "INSERT INTO users (id, username, password_hash, disabled, administrator) "
                        "VALUES (%s, %s, %s, false, %s)",
                        (user_id, username, password_hash, is_admin),
                    )
                    cur.execute(
                        "INSERT INTO project_memberships (project_id, user_id, role) "
                        "VALUES (%s, %s, 'owner')",
                        ("d1861c7e-81b9-441c-a6f9-7a58c96cafd0", user_id),
                    )

            # Revoke any existing sessions for these users
            cur.execute(
                "SELECT id FROM users WHERE username IN ('dev-user', 'dev-reviewer', 'dev-admin')"
            )
            user_ids = [str(r[0]) for r in cur.fetchall()]
            if user_ids:
                cur.execute(
                    "UPDATE auth_sessions SET revoked_at = %s WHERE user_id = ANY(%s) AND revoked_at IS NULL",
                    (now, user_ids),
                )

            # Create new sessions with random tokens
            for username in ["dev-user", "dev-reviewer", "dev-admin"]:
                token = f"oju-dev-{secrets.token_urlsafe(32)}"
                token_hash = hash_token(token)
                session_id = str(uuid4())
                cur.execute(
                    "SELECT id FROM users WHERE username = %s",
                    (username,),
                )
                user_id = str(cur.fetchone()[0])
                cur.execute(
                    "INSERT INTO auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) "
                    "VALUES (%s, %s, %s, %s, NULL, %s)",
                    (session_id, user_id, token_hash, expires_at, now),
                )
                tokens[username] = token

        conn.commit()

    # Write tokens to .env (append, gitignored)
    env_lines = []
    for username, token in tokens.items():
        env_var = f"FORGE_TOKEN_{username.upper().replace('-', '_')}={token}"
        env_lines.append(env_var)

    env_path = ".env"
    with open(env_path, "a") as f:
        f.write("\n# Phase 13 development bearer tokens (gitignored, do not commit)\n")
        for line in env_lines:
            f.write(line + "\n")

    print("Development tokens provisioned:")
    for username, token in tokens.items():
        print(f"  {username}: {token}")
    print(f"\nTokens written to {env_path} (gitignored)")
    print("Use: curl -H 'Authorization: Bearer <token>' ...")


if __name__ == "__main__":
    main()
