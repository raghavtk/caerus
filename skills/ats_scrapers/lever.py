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

_LEVER_HOSTS = {"jobs.lever.co": "https://api.lever.co", "jobs.eu.lever.co": "https://api.eu.lever.co"}
_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json, text/html;q=0.9"}


def parse_lever_url(url: str) -> tuple[str, str, str] | None:
    """Return (site, posting ID, API root) only for official hosted Lever URLs."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None or parsed.hostname.lower() not in _LEVER_HOSTS:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) not in {2, 3} or (len(parts) == 3 and parts[2] != "apply"):
        return None
    site, posting_id = parts[:2]
    if not site or not posting_id:
        return None
    return site, posting_id, _LEVER_HOSTS[parsed.hostname.lower()]


def _category(payload: dict[str, object], key: str) -> str | None:
    categories = payload.get("categories")
    value = categories.get(key) if isinstance(categories, dict) else None
    if value is None:
        value = payload.get(key)
    text = clean_text(value)
    return text or None


def _salary(payload: dict[str, object]) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("salaryDescriptionPlain", "salaryDescription"):
        text = clean_text(payload.get(key))
        if text:
            values.append(text)
    salary = payload.get("salary")
    if isinstance(salary, dict):
        text = clean_text(salary.get("description") or salary.get("text"))
        if text:
            values.append(text)
    salary_range = payload.get("salaryRange")
    if isinstance(salary_range, dict):
        currency = clean_text(salary_range.get("currency"))
        interval = clean_text(salary_range.get("interval"))
        low = salary_range.get("min")
        high = salary_range.get("max")
        if isinstance(low, (int, float)) and isinstance(high, (int, float)):
            prefix = f"{currency} " if currency else ""
            suffix = f" per {interval}" if interval else ""
            values.append(f"{prefix}{low:,.0f} - {high:,.0f}{suffix}")
    return tuple(values)


def _description_from_api(payload: dict[str, object]) -> str:
    sections: list[str] = []
    description = clean_text(payload.get("descriptionPlain"))
    if not description:
        description = html_to_text(str(payload.get("description") or ""))
    if description:
        sections.append(description)
    lists = payload.get("lists")
    if isinstance(lists, list):
        for entry in lists:
            if not isinstance(entry, dict):
                continue
            heading = clean_text(entry.get("text") or entry.get("name") or entry.get("title"))
            content = clean_text(entry.get("contentPlain"))
            if not content:
                content = html_to_text(str(entry.get("content") or ""))
            if heading and content:
                sections.append(f"{heading}\n{content}")
            elif heading:
                sections.append(heading)
            elif content:
                sections.append(content)
    for key in ("additionalPlain", "additional"):
        value = clean_text(payload.get(key)) if key.endswith("Plain") else html_to_text(str(payload.get(key) or ""))
        if value:
            sections.append(value)
            break
    description = clean_text("\n\n".join(sections))
    if not is_usable_description(description):
        raise ValueError("Lever API returned no usable description")
    return description


def _posting_from_api(url: str, posting_id: str, payload: dict[str, object]) -> FetchedJobPosting:
    description = _description_from_api(payload)
    posting = FetchedJobPosting(
        raw_text="",
        ats="Lever",
        source_url=url,
        fetch_source="lever_api",
        job_id=clean_text(payload.get("id") or posting_id) or None,
        company=clean_text(payload.get("company")) or None,
        role=clean_text(payload.get("text") or payload.get("title")) or None,
        location=_category(payload, "location"),
        department=_category(payload, "department"),
        team=_category(payload, "team"),
        employment_type=_category(payload, "commitment"),
        workplace_type=_category(payload, "workplaceType"),
        compensation=_salary(payload),
    )
    return normalized_posting(posting, description)


def _fetch_api(url: str, site: str, posting_id: str, api_root: str) -> FetchedJobPosting:
    endpoint = f"{api_root}/v0/postings/{quote(site, safe='')}/{quote(posting_id, safe='')}"
    with httpx.Client(timeout=20, follow_redirects=True, headers=_HEADERS) as client:
        response = client.get(endpoint)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Lever API returned a non-object response")
    return _posting_from_api(url, posting_id, payload)


def _fetch_html(url: str, posting_id: str) -> FetchedJobPosting:
    with httpx.Client(timeout=20, follow_redirects=True, headers=_HEADERS) as client:
        response = client.get(url)
        response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    sections: list[str] = []
    for section in soup.select("div.section"):
        heading = section.find(["h2", "h3", "h4"])
        content = section.find("div", class_="content") or section
        section_text = html_to_text(str(content))
        if heading:
            title = clean_text(heading.get_text(" ", strip=True))
            if title and not section_text.startswith(title):
                section_text = f"{title}\n{section_text}"
        if section_text:
            sections.append(section_text)
    description = clean_text("\n\n".join(sections))
    if not is_usable_description(description):
        raise ValueError("Lever HTML returned no usable description")
    title = soup.find("h2") or soup.find("h1")
    categories = [clean_text(node.get_text(" ", strip=True)) for node in soup.select("span.posting-category, span.location")]
    posting = FetchedJobPosting(
        raw_text="",
        ats="Lever",
        source_url=url,
        fetch_source="lever_html",
        job_id=posting_id,
        role=clean_text(title.get_text(" ", strip=True)) if title else None,
        location=next((item for item in categories if item), None),
    )
    return normalized_posting(posting, description)


def fetch_lever_posting(url: str) -> FetchedJobPosting:
    parsed = parse_lever_url(url)
    if parsed is None:
        raise ValueError("not a supported Lever URL")
    site, posting_id, api_root = parsed
    try:
        return _fetch_api(url, site, posting_id, api_root)
    except Exception as exc:
        logger.warning("Lever API extraction failed: {}", type(exc).__name__)
    try:
        return _fetch_html(url, posting_id)
    except Exception as exc:
        logger.warning("Lever HTML extraction failed: {}", type(exc).__name__)
        raise


def scrape_lever(url: str) -> str:
    """Compatibility wrapper for callers expecting a normalized text string."""
    return fetch_lever_posting(url).raw_text
