from __future__ import annotations

import html
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup


@dataclass(frozen=True)
class FetchedJobPosting:
    """A complete, provider-normalized job posting used before LLM extraction."""

    raw_text: str
    ats: str | None
    source_url: str | None
    fetch_source: str
    job_id: str | None = None
    company: str | None = None
    role: str | None = None
    location: str | None = None
    department: str | None = None
    team: str | None = None
    employment_type: str | None = None
    workplace_type: str | None = None
    compensation: tuple[str, ...] = ()


def clean_text(value: object | None) -> str:
    """Normalize provider text without removing meaningful repeated content."""
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    for _ in range(3):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    result: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if not blank:
                result.append("")
            blank = True
        else:
            result.append(line)
            blank = False
    return "\n".join(result).strip()


def html_to_text(markup: str) -> str:
    """Retain heading and list order when converting provider HTML to text."""
    soup = BeautifulSoup(markup, "html.parser")
    for unwanted in soup.select("script, style, form, iframe, noscript"):
        unwanted.decompose()
    for item in soup.select("li"):
        item.insert_before("\n- ")
        item.insert_after("\n")
    for block in soup.select("h1, h2, h3, h4, h5, h6, p, div, section, br"):
        block.insert_before("\n")
        block.insert_after("\n")
    return clean_text(soup.get_text())


def is_usable_description(text: str) -> bool:
    """Reject title/category-only pages while accepting compact genuine postings."""
    normalized = clean_text(text)
    return len(normalized) >= 80 and len(normalized.split()) >= 12


def build_normalized_text(posting: FetchedJobPosting, description: str) -> str:
    fields = (
        ("ATS", posting.ats),
        ("Source URL", posting.source_url),
        ("Fetch Source", posting.fetch_source),
        ("Job ID", posting.job_id),
        ("Company", posting.company),
        ("Role", posting.role),
        ("Location", posting.location),
        ("Department", posting.department),
        ("Team", posting.team),
        ("Employment Type", posting.employment_type),
        ("Workplace Type", posting.workplace_type),
    )
    lines = [f"{label}: {value}" for label, value in fields if value]
    lines.extend(f"Compensation: {item}" for item in posting.compensation if item)
    preamble = "\n".join(lines)
    body = clean_text(description)
    return f"{preamble}\n\nJob Description\n{body}".strip()


def normalized_posting(posting: FetchedJobPosting, description: str) -> FetchedJobPosting:
    return FetchedJobPosting(
        raw_text=build_normalized_text(posting, description),
        ats=posting.ats,
        source_url=posting.source_url,
        fetch_source=posting.fetch_source,
        job_id=posting.job_id,
        company=posting.company,
        role=posting.role,
        location=posting.location,
        department=posting.department,
        team=posting.team,
        employment_type=posting.employment_type,
        workplace_type=posting.workplace_type,
        compensation=posting.compensation,
    )
