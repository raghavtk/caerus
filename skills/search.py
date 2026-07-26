from __future__ import annotations

from typing import Any

import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config import get_settings


def web_search(query: str, num_results: int = 5) -> list[dict[str, str]]:
    settings = get_settings()
    provider = settings.search_provider
    if provider == "serper":
        return _serper_search(query=query, num_results=num_results)
    if provider == "tavily":
        return _tavily_search(query=query, num_results=num_results)

    logger.warning("no search provider configured; returning empty search results")
    return []


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=5))
def _serper_search(query: str, num_results: int = 5) -> list[dict[str, str]]:
    settings = get_settings()
    if not settings.serper_api_key:
        return []

    payload: dict[str, Any] = {"q": query, "num": num_results}
    headers = {"X-API-KEY": settings.serper_api_key, "Content-Type": "application/json"}
    with httpx.Client(timeout=20, follow_redirects=True) as client:
        response = client.post("https://google.serper.dev/search", json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

    organic = data.get("organic", []) if isinstance(data, dict) else []
    out: list[dict[str, str]] = []
    for item in organic[:num_results]:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "title": str(item.get("title", "")),
                "snippet": str(item.get("snippet", "")),
                "url": str(item.get("link", "")),
            }
        )
    return out


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=5))
def _tavily_search(query: str, num_results: int = 5) -> list[dict[str, str]]:
    settings = get_settings()
    if not settings.tavily_api_key:
        return []

    payload: dict[str, Any] = {"api_key": settings.tavily_api_key, "query": query, "max_results": num_results}
    headers = {"Content-Type": "application/json"}
    with httpx.Client(timeout=20, follow_redirects=True) as client:
        response = client.post("https://api.tavily.com/search", json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

    results = data.get("results", []) if isinstance(data, dict) else []
    out: list[dict[str, str]] = []
    for item in results[:num_results]:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "title": str(item.get("title", "")),
                "snippet": str(item.get("content", "")),
                "url": str(item.get("url", "")),
            }
        )
    return out
