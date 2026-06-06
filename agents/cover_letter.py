from __future__ import annotations

from config import get_user_profile
from llm import generate_structured
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
        "Structure rule: exactly 3 paragraphs: hook -> fit -> close.\n"
        "Hard rules: max 350 words, no bullets, no generic close, no opener starting with 'I', "
        "must include one specific hook and one specific project."
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
        f"Requirements: {jd.requirements}\n"
        f"Preferred: {jd.preferred}\n"
        f"Soft signals: {jd.domain_signals}\n\n"
        f"Company stage and highlights: {company_brief.model_dump()}\n\n"
        f"Resume selection: {resume_selection.model_dump()}\n\n"
        f"Candidate education: {profile.get('education', [])}\n"
        f"Candidate experience: {profile.get('experience', [])}\n"
        f"Candidate projects: {profile.get('projects', [])}\n"
        f"Candidate publications: {profile.get('publications', [])}"
    )


class _CoverLetterBody(BaseModel):
    body: str


class _HookSummary(BaseModel):
    hook_summary: str


def generate_cover_letter(jd: ParsedJD, company_brief: CompanyBrief, resume_selection: ResumeSelection) -> CoverLetter:
    profile = get_user_profile()
    system = _build_system_prompt(profile)
    user = _build_user_prompt(jd, company_brief, resume_selection, profile)

    body_obj = generate_structured(_CoverLetterBody, system_prompt=system, user_prompt=user, max_tokens=1024)
    summary_obj = generate_structured(
        _HookSummary,
        system_prompt="Summarize the cover letter hook in one sentence.",
        user_prompt=body_obj.body,
        max_tokens=100,
    )
    body = body_obj.body.strip()
    return CoverLetter(
        company=jd.company or "Unknown",
        role=jd.role or "Unknown",
        body=body,
        hook_summary=summary_obj.hook_summary.strip(),
        word_count=len(body.split()),
    )
