from __future__ import annotations

import re
from typing import Any

from config import compact_experience, compact_projects, get_cover_letter_projects, get_user_profile
from llm import generate_text
from schemas.models import CompanyBrief, CoverLetter, ParsedJD, ResumeSelection


MIN_WORDS = 180
MAX_WORDS = 260
_WORD_RE = re.compile(r"\b[\w]+(?:['’][\w]+)?\b", re.UNICODE)
_BULLET_RE = re.compile(r"(?m)^\s*(?:[-*+•]|\d+[.)])\s+")
_MARKUP_RE = re.compile(
    r"(?im)^\s*(?:#{1,6}\s|subject\s*:|>)|"
    r"\[[^\]]+\]\([^\)]+\)|<\/?[a-z][^>]*>|`|\*\*|__|"
    r"(?<!\w)_[^_\n]+_(?!\w)|(?<!\w)\*[^*\n]+\*(?!\w)"
)
_CLICHES = (
    "deeply passionate",
    "perfect fit",
    "dream opportunity",
    "thrilled to apply",
    "excited to apply",
    "thank you for your consideration",
)


class CoverLetterQualityError(ValueError):
    """Raised when a cover letter cannot satisfy the quality contract."""

    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        super().__init__("cover letter failed quality validation: " + "; ".join(violations))


def normalize_cover_letter(body: str) -> str:
    """Normalize line endings and paragraph whitespace without rewriting prose."""
    text = body.replace("\r\n", "\n").replace("\r", "\n").strip()
    paragraphs = [
        re.sub(r"[ \t]+", " ", paragraph.replace("\n", " ")).strip()
        for paragraph in re.split(r"\n\s*\n", text)
    ]
    return "\n\n".join(paragraph for paragraph in paragraphs if paragraph)


def count_words(body: str) -> int:
    return len(_WORD_RE.findall(body))


def _contains_phrase(text: str, phrase: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text, re.IGNORECASE))


def _candidate_identifiers(profile: dict[str, Any]) -> list[str]:
    identifiers: list[str] = []
    for project in get_cover_letter_projects(profile):
        name = str(project.get("name") or "").strip()
        if name:
            identifiers.append(name)
    for experience in profile.get("experience", []):
        for key in ("company", "title"):
            value = str(experience.get(key) or "").strip()
            if value:
                identifiers.append(value)
    return identifiers


def validate_cover_letter(body: str, *, jd: ParsedJD, profile: dict[str, Any]) -> list[str]:
    """Return deterministic, actionable violations for generated cover-letter text."""
    normalized = normalize_cover_letter(body)
    lowered = normalized.casefold()
    paragraphs = normalized.split("\n\n") if normalized else []
    word_count = count_words(normalized)
    violations: list[str] = []

    if len(paragraphs) != 3:
        violations.append(f"use exactly 3 nonempty paragraphs (found {len(paragraphs)})")
    if not MIN_WORDS <= word_count <= MAX_WORDS:
        violations.append(f"use {MIN_WORDS}-{MAX_WORDS} words (found {word_count})")
    if "—" in normalized or "–" in normalized:
        violations.append("remove em and en dashes")
    raw_lines = body.replace("\r\n", "\n").replace("\r", "\n")
    if _BULLET_RE.search(raw_lines):
        violations.append("remove bullets and numbered-list formatting")
    if _MARKUP_RE.search(raw_lines):
        violations.append("remove headings, subject lines, links, HTML, and other markup")
    opening = paragraphs[0].lstrip(" \t\"'“”‘’") if paragraphs else ""
    if opening and re.match(r"(?i)^i(?:\b|['’])", opening):
        violations.append("do not begin the opening paragraph with I")

    voice_profile = profile.get("voice_profile") or {}
    configured_phrases = voice_profile.get("forbidden_phrases") or []
    if isinstance(configured_phrases, str):
        configured_phrases = [configured_phrases]
    forbidden = [*_CLICHES, *configured_phrases]
    found_forbidden = sorted(
        {
            str(phrase)
            for phrase in forbidden
            if str(phrase).strip() and str(phrase).casefold() in lowered
        }
    )
    if found_forbidden:
        violations.append("remove forbidden or generic phrases: " + ", ".join(found_forbidden))

    company = (jd.company or "").strip()
    role = (jd.role or "").strip()
    if not company or not _contains_phrase(normalized, company):
        violations.append("ground the letter in the supplied company name")
    if not role or not _contains_phrase(normalized, role):
        violations.append("ground the letter in the supplied role title")

    identifiers = _candidate_identifiers(profile)
    if not identifiers:
        violations.append("provide at least one usable project or experience identifier in the profile")
    elif not any(_contains_phrase(normalized, identifier) for identifier in identifiers):
        violations.append("mention at least one supplied project or experience identifier exactly")
    return violations


def _build_system_prompt(profile: dict) -> str:
    name = profile.get("name", "Candidate")
    voice_profile = profile.get("voice_profile") or {}
    tone = voice_profile.get("tone", "clear, direct, grounded")
    forbidden = voice_profile.get("forbidden_phrases", [])
    hooks = voice_profile.get("personal_hooks", [])
    return (
        "You write natural, direct, grounded cover letters using only the supplied facts.\n"
        f"Candidate name: {name}\n"
        f"Voice tone: {tone}\n"
        f"Forbidden phrases: {forbidden}\n"
        f"Personal hooks: {hooks}\n"
        f"Structure rule: exactly 3 paragraphs separated by blank lines and "
        f"{MIN_WORDS}-{MAX_WORDS} words: hook -> fit -> close.\n"
        "Hard rules: no bullets, Markdown, headings, subject line, em/en dashes, generic enthusiasm, or opener starting with 'I'. "
        "Name the company and role exactly. Mention at least one supplied project or experience identifier exactly. "
        "Do not invent metrics, technologies, responsibilities, company facts, or personal claims. "
        "Return only the cover-letter body as plain text."
    )


def _build_user_prompt(
    jd: ParsedJD,
    company_brief: CompanyBrief,
    resume_selection: ResumeSelection,
    profile: dict,
) -> str:
    return (
        f"JD Role: {jd.role}\n"
        f"Company: {jd.company}\n"
        f"Requirements: {jd.requirements[:8]}\n"
        f"Preferred: {jd.preferred[:5]}\n"
        f"Soft signals: {jd.domain_signals}\n\n"
        f"Company stage and highlights: {company_brief.model_dump(mode='json')}\n\n"
        f"Resume selection: {resume_selection.model_dump(mode='json')}\n\n"
        f"Candidate education: {profile.get('education', [])}\n"
        f"Candidate experience: {compact_experience(profile.get('experience', []))}\n"
        f"Candidate projects: {compact_projects(get_cover_letter_projects(profile))}\n"
        f"Candidate publications: {profile.get('publications', [])}"
    )


def _build_repair_prompt(*, draft: str, violations: list[str], evidence_prompt: str) -> str:
    rules = "\n".join(f"- {violation}" for violation in violations)
    return (
        "Repair the draft so every listed violation is resolved. Preserve only claims supported by the original evidence.\n\n"
        f"VIOLATIONS:\n{rules}\n\nORIGINAL EVIDENCE:\n{evidence_prompt}\n\nDRAFT:\n{draft}"
    )


def _hook_from_body(body: str) -> str:
    opening = body.split("\n\n", 1)[0]
    match = re.search(r".+?(?:[.!?](?=\s|$)|$)", opening)
    return match.group(0).strip() if match else opening.strip()


def generate_cover_letter(jd: ParsedJD, company_brief: CompanyBrief, resume_selection: ResumeSelection) -> CoverLetter:
    profile = get_user_profile()
    system = _build_system_prompt(profile)
    user = _build_user_prompt(jd, company_brief, resume_selection, profile)

    body = normalize_cover_letter(
        generate_text(system_prompt=system, user_prompt=user, max_tokens=1024, trace_content=False)
    )
    violations = validate_cover_letter(body, jd=jd, profile=profile)
    if violations:
        repair_prompt = _build_repair_prompt(draft=body, violations=violations, evidence_prompt=user)
        body = normalize_cover_letter(
            generate_text(system_prompt=system, user_prompt=repair_prompt, max_tokens=1024, trace_content=False)
        )
        violations = validate_cover_letter(body, jd=jd, profile=profile)
    if violations:
        raise CoverLetterQualityError(violations)

    return CoverLetter(
        company=jd.company or "Unknown",
        role=jd.role or "Unknown",
        body=body,
        hook_summary=_hook_from_body(body),
        word_count=count_words(body),
    )
