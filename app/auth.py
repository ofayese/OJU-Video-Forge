from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from .config import get_settings

bearer_scheme = HTTPBearer(auto_error=False)


class Actor(BaseModel):
    name: str
    roles: list[str] = Field(default_factory=lambda: ["viewer"])
    provider: str = "local"


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
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bearer token required")

        supplied = credentials.credentials.strip()
        for service_name, token in self._token_map.items():
            if token and supplied == token:
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
) -> Actor:
    return get_auth_provider().authenticate(credentials)


def require_roles(*allowed_roles: str):
    def dependency(actor: Actor = Depends(get_current_actor)) -> Actor:
        actor_roles = set(actor.roles)
        if not set(allowed_roles).intersection(actor_roles):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
        return actor

    return dependency
