from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .core import AuthSession, User, get_db

bearer_scheme = HTTPBearer(auto_error=False)
SESSION_LIFETIME = timedelta(hours=12)


class Actor(BaseModel):
    name: str
    roles: list[str] = Field(default_factory=lambda: ["viewer"])
    provider: str = "local"
    user_id: str | None = None
    session_id: str | None = None


class AccountInput(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,63}", normalized):
            raise ValueError("Username must contain 3-64 letters, numbers, dots, underscores, or hyphens.")
        return normalized


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    password_hash = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=64,
    )
    salt_value = base64.urlsafe_b64encode(salt).decode("ascii")
    hash_value = base64.urlsafe_b64encode(password_hash).decode("ascii")
    return f"scrypt$16384$8$1${salt_value}${hash_value}"


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        algorithm, n, r, p, salt_value, hash_value = encoded_hash.split("$")
        if algorithm != "scrypt" or (int(n), int(r), int(p)) != (2**14, 8, 1):
            return False
        salt = base64.urlsafe_b64decode(salt_value.encode("ascii"))
        expected = base64.urlsafe_b64decode(hash_value.encode("ascii"))
        if len(salt) != 16 or len(expected) != 64:
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=2**14,
            r=8,
            p=1,
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, UnicodeError):
        return False


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthProvider:
    def authenticate(self, credentials: HTTPAuthorizationCredentials | None) -> Actor:
        raise NotImplementedError


class LocalAuthProvider(AuthProvider):
    def __init__(self) -> None:
        self.settings = get_settings()
        self._token_map = {
            "openwebui": self.settings.forge_openwebui_token,
            "chatgpt": self.settings.forge_chatgpt_token,
        }

    def authenticate(self, credentials: HTTPAuthorizationCredentials | None) -> Actor:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authorization header required")

        supplied = credentials.credentials.strip()
        for service_name, token in self._token_map.items():
            if token and hmac.compare_digest(supplied.encode("utf-8"), token.encode("utf-8")):
                base_roles = [
                    "viewer",
                    "producer",
                    "reviewer",
                    "publisher",
                    "auditor",
                    "agent_service",
                ]
                if service_name == "chatgpt":
                    base_roles.insert(0, "administrator")
                return Actor(name=service_name, roles=base_roles, provider="local")

        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")


class OIDCAuthProvider(AuthProvider):
    def authenticate(self, credentials: HTTPAuthorizationCredentials | None) -> Actor:
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            "OIDC authentication is not configured; verify the issuer and discovery metadata before enabling enterprise identity.",
        )


def get_auth_provider() -> AuthProvider:
    settings = get_settings()
    if settings.auth_provider == "oidc":
        return OIDCAuthProvider()
    if settings.auth_provider == "azure":
        return OIDCAuthProvider()
    return LocalAuthProvider()


def get_current_actor(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Actor:
    if credentials is not None and db is not None:
        session = db.scalar(
            select(AuthSession).where(
                AuthSession.token_hash == hash_session_token(credentials.credentials.strip())
            )
        )
        if session is not None:
            expires_at = session.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if session.revoked_at is not None or expires_at <= datetime.now(timezone.utc):
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired or revoked")
            user = db.get(User, session.user_id)
            if user is None:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
            return Actor(
                name=user.username,
                roles=["viewer"],
                provider="local",
                user_id=user.id,
                session_id=session.id,
            )
    return get_auth_provider().authenticate(credentials)


def require_roles(*allowed_roles: str):
    def dependency(actor: Actor = Depends(get_current_actor)) -> Actor:
        actor_roles = set(actor.roles)
        if not set(allowed_roles).intersection(actor_roles):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
        return actor

    return dependency
