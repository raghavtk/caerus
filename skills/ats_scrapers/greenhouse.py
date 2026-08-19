from __future__ import annotations

from urllib.parse import quote, urlsplit

from bs4 import BeautifulSoup
import httpx
from loguru import logger

from skills.ats_scrapers.common import (
    FetchedJobPosting,
    clean_text,
    html_to_text,
    is_usable_description,
    normalized_posting,
)

_GREENHOUSE_HOSTS = {"boards.greenhouse.io", "job-boards.greenhouse.io"}
_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json, text/html;q=0.9"}


def parse_greenhouse_url(url: str) -> tuple[str, str] | None:
    """Return (board token, numeric job id) only for official hosted URLs."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None or parsed.hostname.lower() not in _GREENHOUSE_HOSTS:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 3 or parts[1] != "jobs" or not parts[2].isdigit():
        return None
    if not parts[0]:
        return None
    return parts[0], parts[2]


def _names(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    names: list[str] = []
    for item in value:
        if isinstance(item, dict) and item.get("name"):
            names.append(clean_text(item["name"]))
        elif isinstance(item, str) and item.strip():
            names.append(clean_text(item))
    return tuple(name for name in names if name)


def _format_money(value: object, currency: object, *, cents: bool) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    amount = value / 100 if cents else value
    code = clean_text(currency).upper()
    return f"{code + ' ' if code else ''}{amount:,.2f}".rstrip("0").rstrip(".")


def _pay_ranges(payload: dict[str, object]) -> tuple[str, ...]:
    entries = payload.get("pay_input_ranges") or payload.get("pay_transparency") or []
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        return ()
    result: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        currency = entry.get("currency_type") or entry.get("currency")
        low_key = "min_cents" if "min_cents" in entry else "min"
        high_key = "max_cents" if "max_cents" in entry else "max"
        low = _format_money(entry.get(low_key), currency, cents=low_key.endswith("_cents"))
        high = _format_money(entry.get(high_key), currency, cents=high_key.endswith("_cents"))
        label = clean_text(entry.get("title"))
        if low and high:
            text = f"{low} - {high}"
        else:
            text = low or high or clean_text(entry.get("description"))
        if text:
            result.append(f"{label}: {text}" if label else text)
    return tuple(result)


def _posting_from_api(url: str, board: str, job_id: str, payload: dict[str, object]) -> FetchedJobPosting:
    content = clean_text(html_to_text(str(payload.get("content") or "")))
    if not is_usable_description(content):
        raise ValueError("Greenhouse API returned no usable description")
    locations = payload.get("location")
    location = clean_text(locations.get("name")) if isinstance(locations, dict) else clean_text(locations)
    departments = _names(payload.get("departments"))
    offices = _names(payload.get("offices"))
    if not location and offices:
        location = ", ".join(offices)
    company = clean_text(payload.get("company_name") or payload.get("company"))
    posting = FetchedJobPosting(
        raw_text="",
        ats="Greenhouse",
        source_url=url,
        fetch_source="greenhouse_api",
        job_id=clean_text(payload.get("id") or job_id),
        company=company or None,
        role=clean_text(payload.get("title")) or None,
        location=location or None,
        department=", ".join(departments) or None,
        compensation=_pay_ranges(payload),
    )
    return normalized_posting(posting, content)


def _fetch_api(url: str, board: str, job_id: str) -> FetchedJobPosting:
    endpoint = (
        f"https://boards-api.greenhouse.io/v1/boards/{quote(board, safe='')}/jobs/"
        f"{quote(job_id, safe='')}?pay_transparency=true"
    )
    with httpx.Client(timeout=20, follow_redirects=True, headers=_HEADERS) as client:
        response = client.get(endpoint)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Greenhouse API returned a non-object response")
    return _posting_from_api(url, board, job_id, payload)


def _fetch_html(url: str, job_id: str) -> FetchedJobPosting:
    with httpx.Client(timeout=20, follow_redirects=True, headers=_HEADERS) as client:
        response = client.get(url)
        response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    node = (
        soup.find("div", id="content")
        or soup.find("div", class_="job-description")
        or soup.find(attrs={"data-testid": "job-content"})
        or soup.find("main")
        or soup.find("article")
    )
    description = html_to_text(str(node)) if node else ""
    if not is_usable_description(description):
        raise ValueError("Greenhouse HTML returned no usable description")
    h1 = soup.find("h1")
    location = soup.select_one("div.location, .location")
    posting = FetchedJobPosting(
        raw_text="",
        ats="Greenhouse",
        source_url=url,
        fetch_source="greenhouse_html",
        job_id=job_id,
        role=clean_text(h1.get_text(" ", strip=True)) if h1 else None,
        location=clean_text(location.get_text(" ", strip=True)) if location else None,
    )
    return normalized_posting(posting, description)


def fetch_greenhouse_posting(url: str) -> FetchedJobPosting:
    parsed = parse_greenhouse_url(url)
    if parsed is None:
        raise ValueError("not a supported Greenhouse URL")
    board, job_id = parsed
    try:
        return _fetch_api(url, board, job_id)
    except Exception as exc:
        logger.warning("Greenhouse API extraction failed: {}", type(exc).__name__)
    try:
        return _fetch_html(url, job_id)
    except Exception as exc:
        logger.warning("Greenhouse HTML extraction failed: {}", type(exc).__name__)
        raise


def scrape_greenhouse(url: str) -> str:
    """Compatibility wrapper for callers expecting a normalized text string."""
    return fetch_greenhouse_posting(url).raw_text
