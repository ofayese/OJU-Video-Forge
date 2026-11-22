from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

EnvironmentName = Literal["development", "production", "test"]
AuthProviderName = Literal["local", "oidc", "azure"]


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_env: EnvironmentName = "development"
    app_base_url: str = "http://localhost:5173"
    api_base_url: str = "http://localhost:8000"
    auth_provider: AuthProviderName = "local"
    ad_enabled: bool = False
    azure_enabled: bool = False
    external_access_enabled: bool = False
    agents_enabled: bool = False
    agent_provider: Literal["", "ollama"] = ""
    agent_url: str = ""
    agent_model: str = ""
    agent_model_digest: str = ""
    agent_stage_timeout_seconds: int = Field(default=60, ge=10, le=300)

    # Training text inference settings (separate from vision)
    training_text_provider: Literal["", "ollama"] = ""
    training_text_url: str = ""
    training_text_model: str = ""
    training_text_model_digest: str = ""
    training_text_stage_timeout_seconds: int = Field(default=60, ge=10, le=300)
    training_text_generation_timeout_seconds: int = Field(default=300, ge=30, le=900)

    # llama.cpp optional inference provider settings
    llama_cpp_enabled: bool = False
    llama_cpp_url: str = "http://llama-cpp:8080"
    llama_cpp_model: str = ""
    llama_cpp_model_digest: str = ""
    agent_generation_timeout_seconds: int = Field(default=300, ge=30, le=900)
    ad_domain: str = "ojuidc.olutech.systems"
    public_app_fqdn: str = "video.olutechsys.com"
    public_api_fqdn: str = "video-api.olutechsys.com"
    database_url: str = "postgresql+psycopg://forge:forge@localhost:5432/forge"
    redis_url: str = "redis://localhost:6379/0"
    artifact_root: str = "/artifacts"
    review_workflow_enabled: bool = True
    comfyui_url: str = "http://comfyui:8188"
    comfyui_enabled: bool = False
    forge_openwebui_token: str = Field(default="")
    forge_chatgpt_token: str = Field(default="")
    db_password: str = ""

    @field_validator("app_base_url", "api_base_url")
    @classmethod
    def validate_http_url(cls, value: str) -> str:
        if value.startswith(("http://", "https://")):
            return value.rstrip("/")
        raise ValueError("must be a valid absolute HTTP(S) URL")

    @field_validator("public_app_fqdn", "public_api_fqdn", "ad_domain")
    @classmethod
    def validate_hostnames(cls, value: str) -> str:
        host = value.strip()
        if not host or host.startswith(("http://", "https://")):
            raise ValueError("must be a hostname without a scheme")
        return host

    @classmethod
    def load(cls) -> Settings:
        values = {
            "app_env": os.getenv("APP_ENV", "development"),
            "app_base_url": os.getenv("APP_BASE_URL", "http://localhost:5173"),
            "api_base_url": os.getenv("API_BASE_URL", "http://localhost:8000"),
            "auth_provider": os.getenv("AUTH_PROVIDER", "local"),
            "ad_enabled": os.getenv("AD_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
            "azure_enabled": os.getenv("AZURE_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
            "external_access_enabled": os.getenv("EXTERNAL_ACCESS_ENABLED", "false").lower() in {"1", "true", "yes", "on"},

            "training_text_provider": os.getenv("TRAINING_TEXT_PROVIDER", ""),
            "training_text_url": os.getenv("TRAINING_TEXT_URL", ""),
            "training_text_model": os.getenv("TRAINING_TEXT_MODEL", ""),
            "training_text_model_digest": os.getenv("TRAINING_TEXT_MODEL_DIGEST", ""),
            "training_text_stage_timeout_seconds": int(os.getenv("TRAINING_TEXT_STAGE_TIMEOUT_SECONDS", "60")),
            "training_text_generation_timeout_seconds": int(
                os.getenv("TRAINING_TEXT_GENERATION_TIMEOUT_SECONDS", "300")
            ),
            "llama_cpp_enabled": os.getenv("LLAMA_CPP_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
            "llama_cpp_url": os.getenv("LLAMA_CPP_URL", "http://llama-cpp:8080"),
            "llama_cpp_model": os.getenv("LLAMA_CPP_MODEL", ""),
            "llama_cpp_model_digest": os.getenv("LLAMA_CPP_MODEL_DIGEST", ""),
            "agents_enabled": os.getenv("AGENTS_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
            "agent_provider": os.getenv("AGENT_PROVIDER", ""),
            "agent_url": os.getenv("AGENT_URL", ""),
            "agent_model": os.getenv("AGENT_MODEL", ""),
            "agent_model_digest": os.getenv("AGENT_MODEL_DIGEST", ""),
            "agent_stage_timeout_seconds": int(os.getenv("AGENT_STAGE_TIMEOUT_SECONDS", "60")),
            "agent_generation_timeout_seconds": int(
                os.getenv("AGENT_GENERATION_TIMEOUT_SECONDS", "300")
            ),
            "ad_domain": os.getenv("AD_DOMAIN", "ojuidc.olutech.systems"),
            "public_app_fqdn": os.getenv("PUBLIC_APP_FQDN", "video.olutechsys.com"),
            "public_api_fqdn": os.getenv("PUBLIC_API_FQDN", "video-api.olutechsys.com"),
            "database_url": os.getenv("DATABASE_URL", "postgresql+psycopg://forge:forge@localhost:5432/forge"),
            "redis_url": os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            "artifact_root": os.getenv("ARTIFACT_ROOT", "/artifacts"),
            "review_workflow_enabled": os.getenv("REVIEW_WORKFLOW_ENABLED", "true").lower() in {"1", "true", "yes", "on"},
            "comfyui_url": os.getenv("COMFYUI_URL", "http://comfyui:8188"),
            "comfyui_enabled": os.getenv("COMFYUI_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
            "forge_openwebui_token": os.getenv("FORGE_OPENWEBUI_TOKEN", ""),
            "forge_chatgpt_token": os.getenv("FORGE_CHATGPT_TOKEN", ""),
            "db_password": os.getenv("DB_PASSWORD", ""),
        }
        settings = cls.model_validate(values)
        if settings.app_env == "production":
            if not settings.database_url:
                raise ValueError("DATABASE_URL is required in production")
            if not settings.redis_url:
                raise ValueError("REDIS_URL is required in production")
            if not settings.db_password:
                raise ValueError("DB_PASSWORD is required in production")
        return settings


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.load()
    return _settings
