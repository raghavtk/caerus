from __future__ import annotations

from typing import Any

import httpx
from loguru import logger

from config import Settings, get_settings
from schemas.models import ApplicationPackage


def _build_properties(package: ApplicationPackage) -> dict[str, Any]:
    return {
        "Company": {"title": [{"text": {"content": package.jd.company or "Unknown"}}]},
        "Role": {"rich_text": [{"text": {"content": package.jd.role or "Unknown"}}]},
        "Location": {"rich_text": [{"text": {"content": package.jd.location or "Unknown"}}]},
        "Fit Score": {"number": (package.company_brief.fit_score if package.company_brief else None)},
        "Resume Variant": {
            "rich_text": [
                {"text": {"content": str(package.resume_selection.variant) if package.resume_selection else "Unknown"}}
            ]
        },
        "Resume Grade": {"rich_text": [{"text": {"content": package.resume_selection.grade if package.resume_selection else "Unknown"}}]},
        "Hook": {"rich_text": [{"text": {"content": package.cover_letter.hook_summary if package.cover_letter else ""}}]},
        "Output Dir": {"url": package.output_dir if package.output_dir else None},
    }


def _write_via_api(package: ApplicationPackage, settings: Settings) -> ApplicationPackage:
    if not settings.notion_token or not settings.notion_database_id:
        logger.warning("notion token/database id missing; skipping notion write")
        return package

    headers = {
        "Authorization": f"Bearer {settings.notion_token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    body = {
        "parent": {"database_id": settings.notion_database_id},
        "properties": _build_properties(package),
    }
    with httpx.Client(timeout=20, follow_redirects=True) as client:
        response = client.post("https://api.notion.com/v1/pages", headers=headers, json=body)
        response.raise_for_status()
        data = response.json()

    page_id = data.get("id")
    page_url = data.get("url")
    package.notion_page_id = page_id
    package.notion_url = page_url
    return package


def write_to_notion(package: ApplicationPackage) -> ApplicationPackage:
    settings = get_settings()
    try:
        if settings.notion_via_mcp:
            logger.warning("notion MCP configured but direct API path is used in this implementation")
        return _write_via_api(package, settings)
    except Exception as exc:
        logger.warning("notion write failed (non-fatal): {}", exc)
        return package
