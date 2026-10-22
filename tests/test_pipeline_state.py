from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

import agents.orchestrator as pipeline
from schemas.models import ApplicationStatus, CompanyBrief, CoverLetter, ParsedJD, ResumeSelection
from skills.output_writer import OutputWriteError
from skills.pipeline_state import CheckpointError, CheckpointStore, PipelineState


@pytest.fixture
def stages(tmp_path, monkeypatch):
    """Private state and deterministic stand-ins for paid services and publication."""
    root = tmp_path / "outputs"
    monkeypatch.setenv("OUTPUTS_DIR", str(root))
    parse = Mock(return_value=ParsedJD(company="Acme", role="Engineer", raw_text="original job"))
    research = AsyncMock(return_value=CompanyBrief(company="Acme", strong_overlaps=["Python"]))
    select = Mock(return_value=ResumeSelection(talking_points=["Saved selection"]))
    generate = Mock(return_value=CoverLetter(body="Saved opening.\n\nSaved evidence.\n\nSaved closing.", word_count=6))

    def publish(package):
        destination = root / "published"
        destination.mkdir(parents=True, exist_ok=True)
        package.output_dir = str(destination)
        for field, name in (("company_brief_path", "company_brief.md"), ("resume_report_path", "resume_report.md"),
                            ("cover_letter_path", "cover_letter.pdf"), ("cover_letter_docx_path", "cover_letter.docx")):
            path = destination / name
            path.write_text(package.cover_letter.body, encoding="utf-8")
            setattr(package, field, str(path))
        return package

    output = Mock(side_effect=publish)
    notion = Mock(side_effect=lambda package: package)
    bindings = {"parse_jd": parse, "research_company": research, "select_resume": select,
                "generate_cover_letter": generate, "write_outputs": output, "write_to_notion": notion}
    for name, value in bindings.items():
        monkeypatch.setattr(pipeline, name, value)
    return root, bindings, publish


def checkpoint(root):
    paths = list(root.glob(".runs/*/state.json"))
    assert len(paths) == 1
    return paths[0]


@pytest.mark.parametrize("failed", ["parse_jd", "research_company", "select_resume", "generate_cover_letter", "write_outputs"])
async def test_resume_retries_failed_stage_and_preserves_prior_results(stages, failed):
    root, calls, publish = stages
    target = calls[failed]
    original = target.side_effect
    target.side_effect = RuntimeError("interrupted")
    with pytest.raises(RuntimeError, match="interrupted") as error:
        await pipeline.orchestrate("original job", skip_notion=True)
    path = checkpoint(root)
    assert str(path) in " ".join(error.value.__notes__)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["stage"] != "completed"
    order = list(calls)[:5]
    before = {name: call.call_count for name, call in calls.items()}
    target.side_effect = original
    result = await pipeline.orchestrate(resume_from=path)
    assert result.status == ApplicationStatus.COMPLETED
    assert result.jd.company == "Acme"
    assert result.company_brief.strong_overlaps == ["Python"]
    assert result.resume_selection.talking_points == ["Saved selection"]
    assert Path(result.cover_letter_path).read_text() == "Saved opening.\n\nSaved evidence.\n\nSaved closing."
    for name in order[:order.index(failed)]:
        assert calls[name].call_count == before[name], f"completed stage reran: {name}"
    assert calls[failed].call_count == 2
    calls["write_to_notion"].assert_not_called()
    assert json.loads(path.read_text())["stage"] == "completed"


async def test_completed_resume_has_no_effect_and_detects_missing_artifacts(stages):
    root, calls, _ = stages
    first = await pipeline.orchestrate("job", skip_notion=True)
    before = {name: call.call_count for name, call in calls.items()}
    result = await pipeline.orchestrate(resume_from=checkpoint(root))
    assert result == first
    assert {name: call.call_count for name, call in calls.items()} == before
    Path(first.cover_letter_path).unlink()
    with pytest.raises(CheckpointError, match="missing"):
        await pipeline.orchestrate(resume_from=checkpoint(root))
    assert {name: call.call_count for name, call in calls.items()} == before


async def test_cancellation_retains_parse_and_releases_lock(stages):
    root, calls, _ = stages
    entered = asyncio.Event()

    async def blocked(jd):
        entered.set()
        await asyncio.Event().wait()

    calls["research_company"].side_effect = blocked
    task = asyncio.create_task(pipeline.orchestrate("job", skip_notion=True))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    calls["research_company"].side_effect = None
    result = await pipeline.orchestrate(resume_from=checkpoint(root))
    assert result.status == ApplicationStatus.COMPLETED
    calls["parse_jd"].assert_called_once()


async def test_uncertain_notion_is_not_replayed_and_can_be_explicitly_skipped(stages):
    root, calls, _ = stages
    calls["write_to_notion"].side_effect = RuntimeError("lost connection")
    with pytest.raises(RuntimeError, match="lost connection"):
        await pipeline.orchestrate("job")
    path = checkpoint(root)
    assert json.loads(path.read_text())["notion"] == "in_flight"
    with pytest.raises(CheckpointError, match="uncertain"):
        await pipeline.orchestrate(resume_from=path)
    calls["write_to_notion"].assert_called_once()
    result = await pipeline.orchestrate(resume_from=path, skip_notion=True)
    assert result.status == ApplicationStatus.COMPLETED
    calls["write_outputs"].assert_called_once()
    calls["write_to_notion"].assert_called_once()


@pytest.mark.parametrize("payload", ["not json", '{"version": 999}', '{"version": 1, "stage": "completed"}'])
async def test_invalid_state_fails_before_services(stages, payload):
    root, calls, _ = stages
    root.mkdir()
    path = root / "invalid.json"
    path.write_text(payload)
    with pytest.raises(CheckpointError, match="Invalid or unsupported"):
        await pipeline.orchestrate(resume_from=path)
    assert path.read_text() == payload
    for call in calls.values():
        call.assert_not_called()


async def test_inconsistent_progress_cannot_skip_missing_predecessors(stages):
    root, calls, _ = stages
    calls["research_company"].side_effect = RuntimeError("interrupted")
    with pytest.raises(RuntimeError):
        await pipeline.orchestrate("job", skip_notion=True)
    path = checkpoint(root)
    saved = json.loads(path.read_text())
    saved["stage"] = "cover_letter"
    path.write_text(json.dumps(saved))
    before = {name: call.call_count for name, call in calls.items()}
    with pytest.raises(CheckpointError, match="Invalid or unsupported"):
        await pipeline.orchestrate(resume_from=path)
    assert {name: call.call_count for name, call in calls.items()} == before


async def test_checkpoint_failure_stops_downstream_work_and_resumes_previous_commit(stages, monkeypatch):
    root, calls, _ = stages
    replace = os.replace

    def fail_research_commit(source, target):
        if json.loads(Path(source).read_text())["stage"] == "company_research":
            raise OSError("disk unavailable")
        return replace(source, target)

    with monkeypatch.context() as failure:
        failure.setattr("skills.pipeline_state.os.replace", fail_research_commit)
        with pytest.raises(OSError, match="disk unavailable"):
            await pipeline.orchestrate("job", skip_notion=True)
    calls["select_resume"].assert_not_called()
    path = checkpoint(root)
    assert json.loads(path.read_text())["stage"] == "parse_jd"
    result = await pipeline.orchestrate(resume_from=path)
    assert result.status == ApplicationStatus.COMPLETED
    calls["parse_jd"].assert_called_once()
    assert calls["research_company"].call_count == 2
    calls["select_resume"].assert_called_once()


async def test_repeated_fresh_inputs_get_distinct_checkpoints(stages, monkeypatch):
    root, _, _ = stages
    monkeypatch.setattr(pipeline, "make_session_id", lambda _: "same-second-and-input")
    await pipeline.orchestrate("job", skip_notion=True)
    await pipeline.orchestrate("job", skip_notion=True)
    assert len(list(root.glob(".runs/*/state.json"))) == 2


async def test_resume_preserves_completed_stage_accounting(stages):
    from skills.metrics import record_request

    root, calls, publish = stages
    def parse(text):
        record_request("gemini")
        return ParsedJD(company="Acme", role="Engineer")
    async def research(jd):
        record_request("serper")
        return CompanyBrief(company="Acme")
    calls["parse_jd"].side_effect = parse
    calls["research_company"].side_effect = research
    calls["write_outputs"].side_effect = RuntimeError("PDF conversion failed")
    with pytest.raises(RuntimeError, match="PDF conversion failed"):
        await pipeline.orchestrate("job", skip_notion=True)
    path = checkpoint(root)
    saved_metrics = json.loads(path.read_text())["package"]["metrics"]
    assert set(saved_metrics["stages"]) == {"parse_jd", "company_research", "resume_selection", "cover_letter"}
    assert all(stage["status"] == "completed" for stage in saved_metrics["stages"].values())
    assert saved_metrics["stages"]["parse_jd"]["requests"] == {"gemini": 1}
    assert saved_metrics["stages"]["company_research"]["requests"] == {"serper": 1}
    calls["write_outputs"].side_effect = publish
    result = await pipeline.orchestrate(resume_from=path)
    assert result.metrics.model_dump()["stages"]["parse_jd"] == saved_metrics["stages"]["parse_jd"]
    assert result.metrics.model_dump()["stages"]["company_research"] == saved_metrics["stages"]["company_research"]
    assert json.loads(Path(result.output_dir, "metrics.json").read_text()) == result.metrics.model_dump()
    calls["parse_jd"].assert_called_once()
    calls["research_company"].assert_awaited_once()


async def test_metrics_file_failure_resumes_without_repeating_notion_or_publication(stages, monkeypatch):
    root, calls, _ = stages
    original = Path.write_text
    def fail_metrics(path, *args, **kwargs):
        if path.name == "metrics.json":
            raise OSError("metrics unavailable")
        return original(path, *args, **kwargs)
    with monkeypatch.context() as failure:
        failure.setattr(Path, "write_text", fail_metrics)
        with pytest.raises(OSError, match="metrics unavailable"):
            await pipeline.orchestrate("job")
    path = checkpoint(root)
    assert json.loads(path.read_text())["notion"] == "done"
    result = await pipeline.orchestrate(resume_from=path)
    assert result.status == ApplicationStatus.COMPLETED
    assert Path(result.output_dir, "metrics.json").is_file()
    calls["write_outputs"].assert_called_once()
    calls["write_to_notion"].assert_called_once()


def test_failed_atomic_replace_preserves_previous_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    state = PipelineState(session_id="one", input="job", working_directory=str(Path.cwd()))
    store = CheckpointStore(path)
    with store.locked():
        store.save(state)
        state.skip_notion = True
        def fail_replace(*args):
            raise OSError("disk unavailable")
        monkeypatch.setattr("skills.pipeline_state.os.replace", fail_replace)
        with pytest.raises(OSError, match="disk unavailable"):
            store.save(state)
    assert CheckpointStore(path).load().skip_notion is False
    assert list(tmp_path.glob(".state-*.tmp")) == []


def test_lock_excludes_another_process_and_is_released_on_exit(tmp_path):
    path = tmp_path / "state.json"
    code = """
import sys
from pathlib import Path
from skills.pipeline_state import CheckpointStore, CheckpointError
try:
    with CheckpointStore(Path(sys.argv[1])).locked():
        print('acquired')
except CheckpointError:
    print('busy')
"""
    def attempt():
        return subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True,
                              text=True, timeout=20, check=True).stdout.strip()
    with CheckpointStore(path).locked():
        assert attempt() == "busy"
    assert attempt() == "acquired"
    # Abrupt process exit skips Python cleanup; ownership must still be released.
    crash = """
import os, sys
from pathlib import Path
from skills.pipeline_state import CheckpointStore
with CheckpointStore(Path(sys.argv[1])).locked():
    os._exit(7)
"""
    process = subprocess.run([sys.executable, "-c", crash, str(path)], capture_output=True, text=True, timeout=20)
    assert process.returncode == 7, process.stdout + process.stderr
    assert attempt() == "acquired"


async def test_pdf_failure_resumes_real_output_publication_in_fresh_process(stages, monkeypatch, tmp_path):
    """Issue #31: persisted generated content survives a fresh Python interpreter."""
    from skills.output_writer import write_outputs
    root, calls, _ = stages
    monkeypatch.setattr(pipeline, "write_outputs", write_outputs)
    monkeypatch.setattr("skills.output_writer.get_user_profile", lambda: {"name": "Test Applicant"})
    monkeypatch.setattr("skills.output_writer.find_libreoffice", lambda: None)
    with pytest.raises(OutputWriteError, match="pdf_conversion"):
        await pipeline.orchestrate("original job", skip_notion=True)
    path = checkpoint(root)
    state = json.loads(path.read_text())
    assert state["stage"] == "cover_letter"
    assert state["package"]["cover_letter"]["body"] == "Saved opening.\n\nSaved evidence.\n\nSaved closing."
    assert list(root.glob(".diagnostics/*/cover_letter.docx"))
    calls["generate_cover_letter"].assert_called_once()  # Positive observer before restart.
    code = """
import asyncio, sys
from pathlib import Path
import agents.orchestrator as pipeline
import skills.output_writer as writer

def forbidden(*args, **kwargs):
    raise AssertionError('Completed agent stage replayed')
for name in ('parse_jd', 'research_company', 'select_resume', 'generate_cover_letter', 'write_to_notion'):
    setattr(pipeline, name, forbidden)
writer.get_user_profile = lambda: {'name': 'Test Applicant'}
def convert(docx, destination):
    path = destination / 'cover_letter.pdf'
    path.write_bytes(b'synthetic PDF')
    return path
writer._convert_docx_to_pdf = convert
writer._validate_cover_letter_artifacts = lambda *args: 1
package = asyncio.run(pipeline.orchestrate(resume_from=Path(sys.argv[1])))
print(package.model_dump_json())
"""
    env = dict(os.environ, LANGFUSE_PUBLIC_KEY="", LANGFUSE_SECRET_KEY="", OUTPUTS_DIR=str(root))
    process = subprocess.run([sys.executable, "-c", code, str(path)], env=env,
                             capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stdout + process.stderr
    result = json.loads(process.stdout)
    assert result["session_id"] == state["session_id"]
    assert result["status"] == "completed"
    assert Path(result["cover_letter_path"]).is_file()
    from docx import Document
    text = "\n".join(p.text for p in Document(result["cover_letter_docx_path"]).paragraphs)
    assert "Saved opening." in text
    assert "Saved evidence." in text
    assert "Saved closing." in text
