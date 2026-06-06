from __future__ import annotations

from loguru import logger

from config import get_user_profile
from llm import generate_structured
from schemas.models import CompanyBrief, ParsedJD
from skills.search import web_search


def _build_search_queries(company: str, role: str) -> list[str]:
    return [
        f"{company} engineering blog 2024 2025",
        f"{company} tech stack infrastructure engineering",
        f"{company} company culture engineering team",
        f"{company} funding revenue headcount size",
        f"{company} H1B sponsorship visa LCA",
    ]


def research_company(jd: ParsedJD) -> CompanyBrief:
    company = jd.company or "Unknown"
    role = jd.role or "Unknown"
    queries = _build_search_queries(company, role)
    all_results: list[dict[str, str]] = []

    for query in queries:
        try:
            all_results.extend(web_search(query, num_results=5))
        except Exception as exc:
            logger.warning("search failed for query '{}': {}", query, exc)

    context_lines: list[str] = []
    urls: list[str] = []
    for item in all_results:
        url = item.get("url", "")
        if url:
            urls.append(url)
        context_lines.append(f"Title: {item.get('title','')}\nSnippet: {item.get('snippet','')}\nURL: {url}")
    search_context = "\n\n".join(context_lines)[:6000]

    profile = get_user_profile()
    candidate_context = {
        "languages": profile.get("languages", []),
        "domains": profile.get("domains", []),
        "experience": profile.get("experience", []),
        "projects": profile.get("projects", []),
    }

    system_prompt = """
You are a factual company researcher.
Use available evidence from search results.
Rules:
- If unknown, explicitly set fields to Unknown or sparse defaults.
- Provide fit analysis for candidate background.
- Mention H1B/visa sponsorship in sponsorship field if present in data.
- Do not fabricate.
"""
    user_prompt = (
        f"Company: {company}\nRole: {role}\n"
        f"Candidate context: {candidate_context}\n\n"
        f"Search evidence:\n{search_context}"
    )
    brief = generate_structured(CompanyBrief, system_prompt=system_prompt, user_prompt=user_prompt)

    deduped: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url and url not in seen:
            deduped.append(url)
            seen.add(url)
        if len(deduped) >= 10:
            break
    brief.sources = deduped
    return brief
