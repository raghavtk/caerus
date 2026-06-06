from __future__ import annotations

import re
from urllib.parse import quote

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from llm import generate_structured
from loguru import logger
from schemas.models import ParsedJD
from skills.ats_scrapers.greenhouse import scrape_greenhouse
from skills.ats_scrapers.lever import scrape_lever


def _detect_ats(url: str) -> str | None:
    lowered = url.lower()
    if "greenhouse.io" in lowered:
        return "Greenhouse"
    if "jobs.lever.co" in lowered:
        return "Lever"
    if "workday" in lowered:
        return "Workday"
    if "taleo" in lowered:
        return "Taleo"
    if "linkedin.com/jobs" in lowered:
        return "LinkedIn"
    if "ashbyhq.com" in lowered:
        return "Ashby"
    return None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=5))
def _fetch_via_jina(url: str) -> str:
    encoded = quote(url, safe=":/?&=#")
    headers = {"Accept": "text/plain"}
    with httpx.Client(timeout=20, follow_redirects=True, headers=headers) as client:
        response = client.get(f"https://r.jina.ai/{encoded}")
        response.raise_for_status()
        return response.text.strip()


def _fetch_raw(url: str) -> str:
    ats = _detect_ats(url)
    try:
        if ats == "Greenhouse":
            text = scrape_greenhouse(url)
            if text:
                return text
        if ats == "Lever":
            text = scrape_lever(url)
            if text:
                return text
        return _fetch_via_jina(url)
    except Exception as exc:
        raise ValueError(f"failed to fetch job description from URL: {url}") from exc


def parse_jd(url_or_text: str) -> ParsedJD:
    is_url = url_or_text.strip().lower().startswith("http")
    ats = _detect_ats(url_or_text) if is_url else None
    raw_text = _fetch_raw(url_or_text) if is_url else url_or_text
    if not raw_text.strip():
        raise ValueError("job description content is empty")

    system_prompt = """
You extract a structured job description from input text.
Rules:
- Output must match schema.
- domain_signals must be abstract tags from this set only:
  systems, networking, ml, database, security, backend, distributed, ai-infra, data-infra, devtools
- Normalize seniority to exactly one of:
  New Grad, 0-2 yrs, 2-5 yrs, 5+ yrs, Staff+, Unknown
- Use null for missing scalar fields; empty arrays for missing list fields.
- Never invent facts.
"""
    user_prompt = f"Extract job details from this text:\n\n{raw_text[:12000]}"
    parsed = generate_structured(ParsedJD, system_prompt=system_prompt, user_prompt=user_prompt)
    parsed.raw_text = raw_text[:12000]
    if ats and not parsed.ats:
        parsed.ats = ats
    logger.info("parsed JD for company={} role={}", parsed.company, parsed.role)
    return parsed
