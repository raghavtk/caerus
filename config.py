from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from loguru import logger
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"

    serper_api_key: str | None = None
    tavily_api_key: str | None = None
    search_provider: str = "auto"
    search_fallback_on_empty: bool = True
    search_timeout_seconds: int = Field(default=10, ge=1, le=60)
    search_max_attempts: int = Field(default=2, ge=1, le=3)
    search_concurrency: int = Field(default=3, ge=1, le=5)

    notion_token: str | None = None
    notion_database_id: str | None = None
    notion_mcp_url: str | None = None

    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://us.cloud.langfuse.com"

    outputs_dir: str = "outputs"
    resumes_dir: str = "resumes"
    user_profile_path: str = "context/user_profile.yaml"
    profile_archive_path: str = "context/profile_archive.yaml"
    libreoffice_path: str | None = None

    # Optional identity details used in the cover-letter document header.  These
    # are deliberately separate from the profile so users can keep contact
    # information out of repository-tracked context files.
    cover_letter_email: str | None = None
    cover_letter_phone: str | None = None
    cover_letter_location: str | None = None
    cover_letter_linkedin_url: str | None = None
    cover_letter_portfolio_url: str | None = None

    @property
    def notion_via_mcp(self) -> bool:
        return bool(self.notion_mcp_url)

    @field_validator("search_provider", mode="before")
    @classmethod
    def validate_search_provider(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("search_provider must be one of: auto, serper, tavily")
        provider = value.strip().casefold()
        if provider not in {"auto", "serper", "tavily"}:
            raise ValueError("search_provider must be one of: auto, serper, tavily")
        return provider

    @field_validator(
        "cover_letter_email",
        "cover_letter_phone",
        "cover_letter_location",
        "cover_letter_linkedin_url",
        "cover_letter_portfolio_url",
        "libreoffice_path",
        mode="before",
    )
    @classmethod
    def normalize_optional_contact_value(cls, value: object) -> object:
        """Treat whitespace-only environment values as absent contact details."""
        if isinstance(value, str):
            return value.strip() or None
        return value

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

    @property
    def langfuse_mcp_url(self) -> str:
        return f"{self.langfuse_host.rstrip('/')}/api/public/mcp"


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

    explicit_projects = content.get("projects", [])
    grad_projects = content.get("grad_projects", [])
    undergrad_projects = content.get("undergrad_projects", [])
    explicit_projects = explicit_projects if isinstance(explicit_projects, list) else []
    grad_projects = grad_projects if isinstance(grad_projects, list) else []
    undergrad_projects = undergrad_projects if isinstance(undergrad_projects, list) else []
    if grad_projects or undergrad_projects:
        content["projects"] = [*explicit_projects, *grad_projects, *undergrad_projects]
    return content


_TIER_ORDER = {"A": 0, "B": 1, "C": 2}


def _project_tier(project: dict[str, Any]) -> str:
    return str(project.get("tier", "B")).upper()


def _include_in_cover_letter(project: dict[str, Any]) -> bool:
    if "include_in_cover_letter" in project:
        value = project["include_in_cover_letter"]
        if isinstance(value, str):
            return value.strip().casefold() in {"1", "true", "yes", "on"}
        return bool(value)
    return _project_tier(project) != "C"


def get_ranked_projects(profile: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if profile is None:
        profile = get_user_profile()
    raw_projects = profile.get("projects")
    projects = raw_projects if isinstance(raw_projects, list) else []
    projects = [project for project in projects if isinstance(project, dict)]
    return sorted(projects, key=lambda project: _TIER_ORDER.get(_project_tier(project), 1))


def get_cover_letter_projects(profile: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [project for project in get_ranked_projects(profile) if _include_in_cover_letter(project)]


def compact_experience(experience: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for job in experience:
        entry: dict[str, Any] = {
            "company": job.get("company"),
            "title": job.get("title"),
            "dates": job.get("dates"),
            "bullets": (job.get("bullets") or [])[:3],
        }
        stories = job.get("star_stories") or []
        if stories:
            entry["story_titles"] = [story.get("title") for story in stories[:6]]
        compact.append(entry)
    return compact


def compact_projects(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for project in projects:
        description = str(project.get("description", ""))
        if len(description) > 220:
            description = description[:220].rstrip() + "..."
        compact.append(
            {
                "id": project.get("id"),
                "name": project.get("name"),
                "tier": project.get("tier"),
                "stack": project.get("stack"),
                "description": description,
            }
        )
    return compact


def compact_publications(publications: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"title": pub.get("title"), "venue": pub.get("venue")} for pub in publications[:3]]


@lru_cache(maxsize=1)
def get_profile_archive() -> dict[str, Any]:
    settings = get_settings()
    path = Path(settings.profile_archive_path)
    if not path.exists():
        logger.warning("profile archive not found at {}", path)
        return {}

    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # pragma: no cover
        logger.warning("failed reading profile archive {}: {}", path, exc)
        return {}

    if not isinstance(content, dict):
        logger.warning("profile archive content is not a mapping at {}", path)
        return {}
    return content
