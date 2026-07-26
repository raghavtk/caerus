from __future__ import annotations

from loguru import logger

from agents.company_research import research_company
from agents.cover_letter import generate_cover_letter
from agents.jd_parser import parse_jd
from agents.resume_selector import select_resume
from schemas.models import ApplicationPackage, ApplicationStatus
from skills.notion_writer import write_to_notion
from skills.output_writer import write_outputs
from skills.tracing import trace_event


def orchestrate(url_or_text: str, skip_notion: bool = False) -> ApplicationPackage:
    trace_event("pipeline_started", {"skip_notion": skip_notion, "input_preview": url_or_text[:200]})
    logger.info("step 1/6: parse jd")
    jd = parse_jd(url_or_text)
    trace_event("step_parse_jd_completed", {"company": jd.company, "role": jd.role, "ats": jd.ats})

    logger.info("step 2/6: company research")
    brief = research_company(jd)
    trace_event("step_company_research_completed", {"fit_score": brief.fit_score, "stage": str(brief.stage)})

    logger.info("step 3/6: resume selection")
    selection = select_resume(jd, brief)
    trace_event(
        "step_resume_selection_completed",
        {"variant": str(selection.variant), "grade": selection.grade, "fit_score": selection.fit_score},
    )

    logger.info("step 4/6: cover letter generation")
    letter = generate_cover_letter(jd, brief, selection)
    trace_event("step_cover_letter_completed", {"word_count": letter.word_count, "hook": letter.hook_summary})

    package = ApplicationPackage(jd=jd, company_brief=brief, resume_selection=selection, cover_letter=letter)

    logger.info("step 5/6: write outputs")
    package = write_outputs(package)
    package.status = ApplicationStatus.OUTPUTS_WRITTEN
    trace_event("step_write_outputs_completed", {"output_dir": package.output_dir})

    if not skip_notion:
        logger.info("step 6/6: sync notion")
        package = write_to_notion(package)
        package.status = ApplicationStatus.NOTION_SYNCED if package.notion_url else ApplicationStatus.OUTPUTS_WRITTEN
        trace_event("step_write_notion_completed", {"notion_url": package.notion_url})

    package.status = ApplicationStatus.COMPLETED
    trace_event("pipeline_completed", {"status": str(package.status), "output_dir": package.output_dir})
    return package
