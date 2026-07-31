from __future__ import annotations

from config import (
    compact_experience,
    compact_projects,
    compact_publications,
    get_cover_letter_projects,
    get_user_profile,
)
from llm import generate_structured, generate_text
from schemas.models import CompanyBrief, CoverLetter, ParsedJD, ResumeSelection
from pydantic import BaseModel


def _build_system_prompt(profile: dict) -> str:
    name = profile.get("name", "Candidate")
    tone = profile.get("voice_profile", {}).get("tone", "clear, direct, grounded")
    forbidden = profile.get("voice_profile", {}).get("forbidden_phrases", [])
    hooks = profile.get("voice_profile", {}).get("personal_hooks", [])
    return (
        "You write concise, high-impact cover letters.\n"
        f"Candidate name: {name}\n"
        f"Voice tone: {tone}\n"
        f"Forbidden phrases: {forbidden}\n"
        f"Personal hooks: {hooks}\n"
        "Structure rule: exactly 3 paragraphs separated by blank lines: hook -> fit -> close.\n"
        "Hard rules: max 300 words, no bullets, no generic close, no opener starting with 'I', "
        "must include one specific hook and one specific project. "
        "Prefer tier A projects when choosing the specific project; use tier B only if clearly more relevant.\n"
        "Return only the cover letter body text. No JSON, no markdown, no subject line."
    )


def _compact_brief(company_brief: CompanyBrief) -> dict:
    return {
        "company": company_brief.company,
        "stage": company_brief.stage.value,
        "fit_score": company_brief.fit_score,
        "strong_overlaps": company_brief.strong_overlaps[:3],
        "potential_angles": company_brief.potential_angles[:3],
        "tech_highlights": company_brief.tech_highlights[:3],
    }


def _compact_selection(resume_selection: ResumeSelection) -> dict:
    return {
        "variant": resume_selection.variant.value,
        "grade": resume_selection.grade,
        "fit_score": resume_selection.fit_score,
        "talking_points": resume_selection.talking_points[:3],
        "strengths": resume_selection.strengths[:3],
    }


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
        f"Company brief: {_compact_brief(company_brief)}\n\n"
        f"Resume selection: {_compact_selection(resume_selection)}\n\n"
        f"Candidate education: {profile.get('education', [])}\n"
        f"Candidate experience: {compact_experience(profile.get('experience', []))}\n"
        f"Candidate projects: {compact_projects(get_cover_letter_projects(profile))}\n"
        f"Candidate publications: {compact_publications(profile.get('publications', []))}"
    )


class _HookSummary(BaseModel):
    hook_summary: str


def _hook_from_body(body: str) -> str:
    paragraph = body.split("\n\n")[0].strip()
    sentence = paragraph.split(".")[0].strip()
    return sentence + "." if sentence and not sentence.endswith(".") else sentence


def generate_cover_letter(jd: ParsedJD, company_brief: CompanyBrief, resume_selection: ResumeSelection) -> CoverLetter:
    profile = get_user_profile()
    system = _build_system_prompt(profile)
    user = _build_user_prompt(jd, company_brief, resume_selection, profile)

    body = generate_text(system_prompt=system, user_prompt=user, max_tokens=1024).strip()

    try:
        summary_obj = generate_structured(
            _HookSummary,
            system_prompt="Summarize the cover letter opening hook in one sentence.",
            user_prompt=body[:1500],
            max_tokens=256,
        )
        hook_summary = summary_obj.hook_summary.strip()
    except ValueError:
        hook_summary = _hook_from_body(body)

    return CoverLetter(
        company=jd.company or "Unknown",
        role=jd.role or "Unknown",
        body=body,
        hook_summary=hook_summary,
        word_count=len(body.split()),
    )
