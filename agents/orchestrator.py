from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from uuid import uuid4

from loguru import logger

from agents.company_research import research_company
from agents.cover_letter import generate_cover_letter
from agents.jd_parser import parse_jd
from agents.resume_selector import select_resume
from config import get_settings
from schemas.models import ApplicationPackage, ApplicationStatus
from skills.metrics import RunMetrics
from skills.notion_writer import write_to_notion
from skills.output_writer import write_outputs
from skills.pipeline_state import CheckpointError, CheckpointStore, PipelineState, Stage
from skills.tracing import make_session_id, pipeline_trace


async def orchestrate(
    url_or_text: str | None = None,
    skip_notion: bool = False,
    *,
    resume_from: Path | None = None,
    replay=None,
) -> ApplicationPackage:
    """Run or explicitly resume a local pipeline, retaining each successful stage."""
    if resume_from is not None:
        if url_or_text is not None or replay is not None:
            raise ValueError("Provide either job input/replay or resume_from, not both")
        if not Path(resume_from).is_file():
            raise CheckpointError(f"Checkpoint not found: {resume_from}")
        store = CheckpointStore(Path(resume_from))
    else:
        if not url_or_text or not url_or_text.strip():
            raise ValueError("Job input must not be blank")
        session_id = f"{make_session_id(url_or_text)}-{uuid4().hex[:8]}"
        state = PipelineState(session_id=session_id, input=url_or_text,
                              working_directory=str(Path.cwd().resolve()), skip_notion=skip_notion)
        if replay is not None:
            # Synthetic replay owns its destination and never reads production settings.
            return await _continue(state, None, True, replay=replay)
        store = CheckpointStore(Path(get_settings().outputs_dir) / ".runs" / session_id / "state.json")

    with store.locked():
        if resume_from is not None:
            state = store.load()
            if Path(state.working_directory).resolve() != Path.cwd().resolve():
                raise CheckpointError("Resume from the original working directory: " + state.working_directory)
        else:
            store.save(state)

        logger.info("Pipeline checkpoint: {}", store.path)
        try:
            return await _continue(state, store, skip_notion)
        except BaseException as exc:
            # Includes cancellation; the previous durable snapshot remains authoritative.
            exc.add_note(f'Resume with: caerus run --resume "{store.path}"')
            raise


async def _continue(
    state: PipelineState, store: CheckpointStore | None, skip_notion: bool, *, replay=None
) -> ApplicationPackage:
    def checkpoint(stage: Stage | None = None) -> None:
        if stage is not None:
            state.stage = stage
        if store is not None:
            store.save(state)

    skip_notion = skip_notion or state.skip_notion
    if state.notion == "in_flight" and not skip_notion:
        raise CheckpointError("Notion outcome is uncertain. Check Notion before resuming with --no-notion; "
                              "automatic retry could create a duplicate page.")
    if state.reached("write_outputs"):
        package = state.package
        # Never silently skip output writing when the published artifacts were removed.
        if not package.output_dir or not Path(package.output_dir).is_dir():
            raise CheckpointError("Published outputs are missing; restore them or start a new run")
        for field in ("company_brief_path", "resume_report_path", "cover_letter_path", "cover_letter_docx_path"):
            value = getattr(package, field)
            if not value or not Path(value).is_file():
                raise CheckpointError("Published outputs are missing; restore them or start a new run")
        if package.selected_resume_copy_path and not Path(package.selected_resume_copy_path).is_file():
            raise CheckpointError("Published resume is missing; restore it or start a new run")
    if state.stage == "completed":
        return state.package

    # Persist an explicit opt-out before doing more work, so later resumes retain it.
    state.skip_notion = skip_notion
    checkpoint()
    metrics = state.package.metrics if state.package is not None else RunMetrics()
    trace_context = pipeline_trace(
        session_id=state.session_id, input_preview=state.input[:200], skip_notion=skip_notion
    ) if replay is None else nullcontext(replay)
    with trace_context as trace:
        if not state.reached("parse_jd"):
            logger.info("step 1/6: parse jd")
            with metrics.stage("parse_jd"), trace.step("parse_jd") as step:
                jd = (replay.parse_jd if replay is not None else parse_jd)(state.input)
                state.package = ApplicationPackage(jd=jd, session_id=state.session_id, metrics=metrics)
                step.set_output({"company": jd.company, "role": jd.role, "ats": jd.ats})
            checkpoint("parse_jd")

        package = state.package
        package.checkpoint_path = str(store.path) if store is not None else None
        package.trace_id = trace.trace_id
        package.trace_url = trace.trace_url
        jd = package.jd
        if not state.reached("company_research"):
            logger.info("step 2/6: company research")
            with metrics.stage("company_research"), trace.step("company_research") as step:
                package.company_brief = await (replay.research_company if replay is not None else research_company)(jd)
                step.set_output({"fit_score": package.company_brief.fit_score, "stage": str(package.company_brief.stage)})
            checkpoint("company_research")

        if not state.reached("resume_selection"):
            logger.info("step 3/6: resume selection")
            with metrics.stage("resume_selection"), trace.step("resume_selection") as step:
                package.resume_selection = (replay.select_resume if replay is not None else select_resume)(jd, package.company_brief)
                selection = package.resume_selection
                step.set_output({"variant": str(selection.variant), "grade": selection.grade, "fit_score": selection.fit_score})
            checkpoint("resume_selection")

        if not state.reached("cover_letter"):
            logger.info("step 4/6: cover letter generation")
            with metrics.stage("cover_letter"), trace.step("cover_letter") as step:
                package.cover_letter = (replay.generate_cover_letter if replay is not None else generate_cover_letter)(
                    jd, package.company_brief, package.resume_selection
                )
                package.optimization_diagnostics = package.cover_letter.optimization_diagnostics
                step.set_output({"word_count": package.cover_letter.word_count})
            checkpoint("cover_letter")

        if not state.reached("write_outputs"):
            logger.info("step 5/6: write outputs")
            with metrics.stage("write_outputs"), trace.step("write_outputs") as step:
                package = (replay.write_outputs if replay is not None else write_outputs)(package)
                state.package = package
                package.metrics = metrics
                package.status = ApplicationStatus.OUTPUTS_WRITTEN
                step.set_output({"output_dir": package.output_dir})
            checkpoint("write_outputs")

        if skip_notion:
            state.notion = "skipped"
        elif state.notion == "pending":
            logger.info("step 6/6: sync notion")
            state.notion = "in_flight"
            checkpoint()
            with metrics.stage("write_notion"), trace.step("write_notion") as step:
                package = write_to_notion(package)
                state.package = package
                package.metrics = metrics
                package.status = ApplicationStatus.NOTION_SYNCED if package.notion_url else ApplicationStatus.OUTPUTS_WRITTEN
                step.set_output({"notion_url": package.notion_url})
            state.notion = "done"
            checkpoint()

        if package.output_dir:
            Path(package.output_dir, "metrics.json").write_text(metrics.model_dump_json(indent=2) + "\n", encoding="utf-8")
        package.status = ApplicationStatus.COMPLETED
        checkpoint("completed")
        trace.set_output({"status": str(package.status), "output_dir": package.output_dir, "session_id": state.session_id})
        return package
