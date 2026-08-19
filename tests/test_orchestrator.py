from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agents.cover_letter import CoverLetterQualityError
from agents.orchestrator import orchestrate
from schemas.models import CompanyBrief, ParsedJD, ResumeSelection


@patch("agents.orchestrator.write_outputs")
@patch("agents.orchestrator.generate_cover_letter", side_effect=CoverLetterQualityError(["invalid draft"]))
@patch("agents.orchestrator.select_resume", return_value=ResumeSelection())
@patch("agents.orchestrator.research_company", return_value=CompanyBrief(company="Acme"))
@patch("agents.orchestrator.parse_jd", return_value=ParsedJD(company="Acme", role="Engineer"))
@patch("agents.orchestrator.pipeline_trace")
def test_quality_failure_prevents_output_writes(
    mock_pipeline_trace,
    mock_parse,
    mock_research,
    mock_select,
    mock_cover,
    mock_write_outputs,
) -> None:
    trace = MagicMock()
    mock_pipeline_trace.return_value.__enter__.return_value = trace
    trace.step.return_value.__enter__.return_value = MagicMock()

    with pytest.raises(CoverLetterQualityError):
        orchestrate("job text", skip_notion=True)

    mock_cover.assert_called_once()
    mock_write_outputs.assert_not_called()


@patch("agents.orchestrator.write_outputs")
@patch("agents.orchestrator.generate_cover_letter")
@patch("agents.orchestrator.select_resume", return_value=ResumeSelection())
@patch("agents.orchestrator.research_company", return_value=CompanyBrief(company="Acme"))
@patch("agents.orchestrator.parse_jd", return_value=ParsedJD(company="Acme", role="Engineer"))
@patch("agents.orchestrator.pipeline_trace")
def test_cover_step_trace_excludes_personal_letter_content(
    mock_pipeline_trace,
    mock_parse,
    mock_research,
    mock_select,
    mock_cover,
    mock_write_outputs,
) -> None:
    from schemas.models import ApplicationPackage, CoverLetter

    letter = CoverLetter(body="private body", hook_summary="private hook", word_count=200)
    mock_cover.return_value = letter
    mock_write_outputs.side_effect = lambda package: package
    trace = MagicMock()
    trace.trace_id = None
    trace.trace_url = None
    step = MagicMock()
    mock_pipeline_trace.return_value.__enter__.return_value = trace
    trace.step.return_value.__enter__.return_value = step

    result = orchestrate("job text", skip_notion=True)

    assert isinstance(result, ApplicationPackage)
    cover_outputs = [
        call.args[0]
        for call in step.set_output.call_args_list
        if call.args and call.args[0].get("word_count") == 200
    ]
    assert cover_outputs == [{"word_count": 200}]
    assert all("private" not in str(call) for call in step.set_output.call_args_list)
