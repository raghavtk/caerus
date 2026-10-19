from __future__ import annotations

from loguru import logger
from pathlib import Path
from contextlib import nullcontext
from skills.metrics import RunMetrics

from agents.company_research import research_company
from agents.cover_letter import generate_cover_letter
from agents.jd_parser import parse_jd
from agents.resume_selector import select_resume
from schemas.models import ApplicationPackage, ApplicationStatus
from skills.notion_writer import write_to_notion
from skills.output_writer import write_outputs
from skills.tracing import make_session_id, pipeline_trace


async def orchestrate(url_or_text: str, skip_notion: bool = False, *, replay=None) -> ApplicationPackage:
    metrics = RunMetrics()
    session_id = make_session_id(url_or_text)
    if replay is not None:
        skip_notion = True
    trace_context = pipeline_trace(
        session_id=session_id,
        input_preview=url_or_text[:200],
        skip_notion=skip_notion,
    ) if replay is None else nullcontext(replay)
    with trace_context as trace:
        logger.info("step 1/6: parse jd")
        with metrics.stage("parse_jd"), trace.step("parse_jd") as step:
            jd = (replay.parse_jd if replay is not None else parse_jd)(url_or_text)
            step.set_output({"company": jd.company, "role": jd.role, "ats": jd.ats})

        logger.info("step 2/6: company research")
        with metrics.stage("company_research"), trace.step("company_research") as step:
            brief = await (replay.research_company if replay is not None else research_company)(jd)
            step.set_output({"fit_score": brief.fit_score, "stage": str(brief.stage)})

        logger.info("step 3/6: resume selection")
        with metrics.stage("resume_selection"), trace.step("resume_selection") as step:
            selection = (replay.select_resume if replay is not None else select_resume)(jd, brief)
            step.set_output(
                {
                    "variant": str(selection.variant),
                    "grade": selection.grade,
                    "fit_score": selection.fit_score,
                }
            )

        logger.info("step 4/6: cover letter generation")
        with metrics.stage("cover_letter"), trace.step("cover_letter") as step:
            letter = (replay.generate_cover_letter if replay is not None else generate_cover_letter)(jd, brief, selection)
            step.set_output({"word_count": letter.word_count})

        package = ApplicationPackage(
            jd=jd,
            company_brief=brief,
            resume_selection=selection,
            cover_letter=letter,
            optimization_diagnostics=letter.optimization_diagnostics,
            session_id=session_id,
            trace_id=trace.trace_id,
            trace_url=trace.trace_url,
        )

        logger.info("step 5/6: write outputs")
        with metrics.stage("write_outputs"), trace.step("write_outputs") as step:
            package = (replay.write_outputs if replay is not None else write_outputs)(package)
            package.status = ApplicationStatus.OUTPUTS_WRITTEN
            step.set_output({"output_dir": package.output_dir})

        if not skip_notion:
            logger.info("step 6/6: sync notion")
            with metrics.stage("write_notion"), trace.step("write_notion") as step:
                package = write_to_notion(package)
                package.status = (
                    ApplicationStatus.NOTION_SYNCED if package.notion_url else ApplicationStatus.OUTPUTS_WRITTEN
                )
                step.set_output({"notion_url": package.notion_url})

        package.metrics = metrics
        if package.output_dir:
            Path(package.output_dir, "metrics.json").write_text(metrics.model_dump_json(indent=2) + "\n", encoding="utf-8")
        package.status = ApplicationStatus.COMPLETED
        trace.set_output(
            {
                "status": str(package.status),
                "output_dir": package.output_dir,
                "session_id": session_id,
            }
        )
        return package
