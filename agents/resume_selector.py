from __future__ import annotations

from pathlib import Path

from loguru import logger

from config import compact_experience, compact_projects, get_ranked_projects, get_settings, get_user_profile
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


def _variant_description(entry: object) -> str:
    if isinstance(entry, dict):
        return str(entry.get("description", ""))
    return str(entry)


def _resolve_resume_path(variant: ResumeVariant, profile: dict, resumes_dir: str) -> Path:
    variants = profile.get("resume_variants", {})
    entry = variants.get(variant.value)

    if isinstance(entry, dict) and entry.get("file"):
        path = Path(resumes_dir) / str(entry["file"])
    else:
        path = Path(resumes_dir) / f"{variant.value.lower()}.pdf"

    if path.exists():
        return path

    general_entry = variants.get(ResumeVariant.GENERAL.value)
    if variant != ResumeVariant.GENERAL and isinstance(general_entry, dict) and general_entry.get("file"):
        fallback = Path(resumes_dir) / str(general_entry["file"])
        if fallback.exists():
            logger.warning("resume file missing for {}; falling back to GENERAL at {}", variant, fallback)
            return fallback

    return path


def select_resume(jd: ParsedJD, company_brief: CompanyBrief | None = None) -> ResumeSelection:
    profile = get_user_profile()
    heuristic = _heuristic_select(jd, profile)

    variant_desc = {key: _variant_description(value) for key, value in profile.get("resume_variants", {}).items()}
    candidate_context = {
        "education": profile.get("education", []),
        "experience": compact_experience(profile.get("experience", [])),
        "projects": compact_projects(get_ranked_projects(profile)),
        "skills": profile.get("skills", []),
    }
    brief_summary = company_brief.model_dump() if company_brief else {}
    system_prompt = """
You are an expert recruiter selecting the best resume variant.
Rules:
- Grade honestly using only A, B, C, D, or F (no plus/minus).
- A gap is a gap only if explicitly required by the JD.
- strengths, gaps, and talking_points: max 3 items each, under 20 words per item.
- Return structured output only.
"""
    user_prompt = (
        f"JD metadata: {jd.model_dump()}\n"
        f"Company brief: {brief_summary}\n"
        f"Resume variant descriptions: {variant_desc}\n"
        f"Candidate background: {candidate_context}\n"
        f"Heuristic hint: {heuristic.value if heuristic else 'None'}"
    )
    selection = generate_structured(
        ResumeSelection,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=2048,
    )

    if heuristic and selection.variant == ResumeVariant.GENERAL:
        selection.variant = heuristic

    settings = get_settings()
    selected_path = _resolve_resume_path(selection.variant, profile, settings.resumes_dir)
    selection.selected_resume_path = str(selected_path)
    if not selected_path.exists():
        logger.warning("selected resume file not found at {}", selected_path)
    else:
        logger.info("selected resume path {}", selected_path)

    return selection
