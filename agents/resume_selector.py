from __future__ import annotations

from pathlib import Path

from loguru import logger

from config import get_settings, get_user_profile
from llm import generate_structured
from schemas.models import CompanyBrief, ParsedJD, ResumeSelection, ResumeVariant


def _heuristic_select(jd: ParsedJD, profile: dict) -> ResumeVariant | None:
    signals = {s.lower() for s in jd.domain_signals}
    company = (jd.company or "").lower()

    if "networking" in signals or "security" in signals:
        return ResumeVariant.NETWORK_SECURITY
    if "database" in signals or "db" in company:
        return ResumeVariant.DATABASE
    if "ml" in signals or "ai-infra" in signals:
        return ResumeVariant.AI_ML
    if "systems" in signals:
        return ResumeVariant.SYSTEMS
    return None


def select_resume(jd: ParsedJD, company_brief: CompanyBrief | None = None) -> ResumeSelection:
    profile = get_user_profile()
    heuristic = _heuristic_select(jd, profile)

    variant_desc = profile.get("resume_variants", {})
    candidate_context = {
        "education": profile.get("education", []),
        "experience": profile.get("experience", []),
        "projects": profile.get("projects", []),
        "skills": profile.get("skills", []),
    }
    brief_summary = company_brief.model_dump() if company_brief else {}
    system_prompt = """
You are an expert recruiter selecting the best resume variant.
Rules:
- Grade honestly (A-F).
- A gap is a gap only if explicitly required by the JD.
- Provide specific talking points for a targeted cover letter.
- Return structured output only.
"""
    user_prompt = (
        f"JD metadata: {jd.model_dump()}\n"
        f"Company brief: {brief_summary}\n"
        f"Resume variant descriptions: {variant_desc}\n"
        f"Candidate background: {candidate_context}\n"
        f"Heuristic hint: {heuristic.value if heuristic else 'None'}"
    )
    selection = generate_structured(ResumeSelection, system_prompt=system_prompt, user_prompt=user_prompt)

    if heuristic and selection.variant == ResumeVariant.GENERAL:
        selection.variant = heuristic

    settings = get_settings()
    selected_name = f"{selection.variant.value.lower()}.pdf"
    selected_path = Path(settings.resumes_dir) / selected_name
    selection.selected_resume_path = str(selected_path)
    if not selected_path.exists():
        logger.warning("selected resume file not found at {}", selected_path)
    else:
        logger.info("selected resume path {}", selected_path)

    return selection
