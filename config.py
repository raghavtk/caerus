from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from loguru import logger
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"

    serper_api_key: str | None = None
    tavily_api_key: str | None = None

    notion_token: str | None = None
    notion_database_id: str | None = None
    notion_mcp_url: str | None = None

    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    outputs_dir: str = "outputs"
    resumes_dir: str = "resumes"
    user_profile_path: str = "context/user_profile.yaml"

    @property
    def notion_via_mcp(self) -> bool:
        return bool(self.notion_mcp_url)

    @property
    def search_provider(self) -> str:
        if self.serper_api_key:
            return "serper"
        if self.tavily_api_key:
            return "tavily"
        return "none"

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def get_user_profile() -> dict[str, Any]:
    settings = get_settings()
    path = Path(settings.user_profile_path)
    if not path.exists():
        logger.warning("user profile not found at {}", path)
        return {}

    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # pragma: no cover
        logger.warning("failed reading user profile {}: {}", path, exc)
        return {}

    if not isinstance(content, dict):
        logger.warning("user profile content is not a mapping at {}", path)
        return {}
    return content
