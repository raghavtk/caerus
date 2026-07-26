from __future__ import annotations

import pytest

from skills.agent_eval import list_fixture_ids, run_agent_eval

# Live evals hit Gemini (and company_research also hits search).
# Gated by CAERUS_ALLOW_LIVE=1 (see tests/conftest.py) — deselected otherwise.
#
# NEVER run these from an agent session unless the user explicitly asks.
# Approximate cost per fixture:
#   jd_parser:        1 Gemini call
#   resume_selector:  2 Gemini calls (parse + select)
#   cover_letter:     3–4 Gemini calls (parse + select + letter + hook)
#   company_research: 1 Gemini + up to 5 search queries


@pytest.mark.live
@pytest.mark.parametrize("fixture_id", list_fixture_ids())
def test_live_jd_parser(fixture_id: str) -> None:
    report = run_agent_eval("jd_parser", fixture_id)
    failed = [c for c in report.checks if not c.ok]
    assert report.passed, f"{fixture_id}: {[c.name + ': ' + c.detail for c in failed]}"


@pytest.mark.live
@pytest.mark.parametrize("fixture_id", list_fixture_ids())
def test_live_resume_selector(fixture_id: str) -> None:
    report = run_agent_eval("resume_selector", fixture_id)
    failed = [c for c in report.checks if not c.ok]
    assert report.passed, f"{fixture_id}: {[c.name + ': ' + c.detail for c in failed]}"


@pytest.mark.live
@pytest.mark.parametrize("fixture_id", list_fixture_ids())
def test_live_cover_letter(fixture_id: str) -> None:
    report = run_agent_eval("cover_letter", fixture_id)
    failed = [c for c in report.checks if not c.ok]
    assert report.passed, f"{fixture_id}: {[c.name + ': ' + c.detail for c in failed]}"


@pytest.mark.live
@pytest.mark.parametrize("fixture_id", ["systems_cloudflare"])
def test_live_company_research_smoke(fixture_id: str) -> None:
    """Single-fixture smoke only — company research is search-heavy."""
    report = run_agent_eval("company_research", fixture_id)
    failed = [c for c in report.checks if not c.ok]
    assert report.passed, f"{fixture_id}: {[c.name + ': ' + c.detail for c in failed]}"
