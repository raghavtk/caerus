from __future__ import annotations

from urllib.parse import quote, urlsplit

import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from llm import generate_structured
from schemas.models import ParsedJD
from skills.ats_scrapers.common import FetchedJobPosting, normalized_posting
from skills.ats_scrapers.greenhouse import fetch_greenhouse_posting
from skills.ats_scrapers.lever import fetch_lever_posting


def _detect_ats(url: str) -> str | None:
    """Recognize known ATS hosts without matching lookalike domains."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    parts = [part for part in parsed.path.split("/") if part]
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        return "Greenhouse"
    if host in {"jobs.lever.co", "jobs.eu.lever.co"}:
        return "Lever"
    if host.endswith(".workdayjobs.com") or host.endswith(".myworkdayjobs.com"):
        return "Workday"
    if host == "taleo.net" or host.endswith(".taleo.net"):
        return "Taleo"
    if host in {"linkedin.com", "www.linkedin.com"} and parts and parts[0] == "jobs":
        return "LinkedIn"
    if host == "ashbyhq.com" or host.endswith(".ashbyhq.com"):
        return "Ashby"
    return None


def _is_http_url(value: str) -> bool:
    parsed = urlsplit(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=5))
def _fetch_via_jina(url: str) -> str:
    encoded = quote(url, safe=":/?&=#")
    headers = {"Accept": "text/plain"}
    with httpx.Client(timeout=20, follow_redirects=True, headers=headers) as client:
        response = client.get(f"https://r.jina.ai/{encoded}")
        response.raise_for_status()
        text = response.text.strip()
    if not text:
        raise ValueError("Jina returned empty content")
    return text


def _fetch_job(url: str) -> FetchedJobPosting:
    ats = _detect_ats(url)
    provider_fetch = {
        "Greenhouse": fetch_greenhouse_posting,
        "Lever": fetch_lever_posting,
    }.get(ats)
    if provider_fetch is not None:
        try:
            return provider_fetch(url)
        except Exception as exc:
            logger.warning("{} provider API/HTML extraction failed: {}", ats, type(exc).__name__)

    try:
        raw_text = _fetch_via_jina(url)
        posting = FetchedJobPosting(raw_text="", ats=ats, source_url=url, fetch_source="jina")
        return normalized_posting(posting, raw_text)
    except Exception as exc:
        logger.warning("Jina extraction failed: {}", type(exc).__name__)
        raise ValueError(f"failed to fetch job description from URL: {url}") from exc


def _fetch_raw(url: str) -> str:
    """Compatibility wrapper retained for existing callers and tests."""
    return _fetch_job(url).raw_text


def parse_jd(url_or_text: str) -> ParsedJD:
    value = url_or_text.strip()
    if not value:
        raise ValueError("job description content is empty")
    if _is_http_url(value):
        posting = _fetch_job(value)
    else:
        posting = FetchedJobPosting(raw_text=url_or_text, ats=None, source_url=None, fetch_source="pasted")
    if not posting.raw_text.strip():
        raise ValueError("job description content is empty")

    system_prompt = """
You extract a structured job description from input text.
Rules:
- Output must match schema field names exactly.
- Capture every distinct explicit required qualification and every distinct preferred/nice-to-have qualification.
- Preserve technologies, experience ranges, education, clearance, and eligibility requirements when present.
- Do not merge unrelated bullets into one item.
- domain_signals must be abstract tags from this set only:
  systems, networking, ml, database, security, backend, distributed, ai-infra, data-infra, devtools
- Normalize seniority to exactly one of:
  New Grad, 0-2 yrs, 2-5 yrs, 5+ yrs, Staff+, Unknown
- Use null for missing scalar fields; empty arrays for missing list fields.
- Never invent facts not supported by the text.
"""
    user_prompt = f"Extract job details from this text:\n\n{posting.raw_text}"
    parsed = generate_structured(
        ParsedJD,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=4096,
    )
    parsed.raw_text = posting.raw_text
    for field in ("company", "role", "location", "ats"):
        provider_value = getattr(posting, field)
        if provider_value:
            setattr(parsed, field, provider_value)
    logger.info("parsed JD for company={} role={}", parsed.company, parsed.role)
    return parsed
