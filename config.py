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
    langfuse_host: str = "https://us.cloud.langfuse.com"

    outputs_dir: str = "outputs"
    resumes_dir: str = "resumes"
    user_profile_path: str = "context/user_profile.yaml"
    profile_archive_path: str = "context/profile_archive.yaml"

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

    grad_projects = content.get("grad_projects", [])
    undergrad_projects = content.get("undergrad_projects", [])
    if grad_projects or undergrad_projects:
        content["projects"] = [*grad_projects, *undergrad_projects]
    return content


_TIER_ORDER = {"A": 0, "B": 1, "C": 2}


def _project_tier(project: dict[str, Any]) -> str:
    return str(project.get("tier", "B")).upper()


def _include_in_cover_letter(project: dict[str, Any]) -> bool:
    if "include_in_cover_letter" in project:
        return bool(project["include_in_cover_letter"])
    return _project_tier(project) != "C"


def get_ranked_projects(profile: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    profile = profile or get_user_profile()
    projects = profile.get("projects", [])
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
