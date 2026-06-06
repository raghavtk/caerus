from __future__ import annotations

from unittest.mock import patch

from agents.resume_selector import _heuristic_select, select_resume
from schemas.models import ParsedJD, ResumeSelection, ResumeVariant


def _jd(signals: list[str], company: str = "Acme") -> ParsedJD:
    return ParsedJD(company=company, role="SE", domain_signals=signals, raw_text="x")


def test_heuristic_network_security() -> None:
    assert _heuristic_select(_jd(["networking"]), {}) == ResumeVariant.NETWORK_SECURITY


def test_heuristic_database() -> None:
    assert _heuristic_select(_jd(["database"]), {}) == ResumeVariant.DATABASE


def test_heuristic_ml() -> None:
    assert _heuristic_select(_jd(["ml"]), {}) == ResumeVariant.AI_ML


def test_heuristic_systems() -> None:
    assert _heuristic_select(_jd(["systems"]), {}) == ResumeVariant.SYSTEMS


def test_heuristic_ambiguous() -> None:
    assert _heuristic_select(_jd(["backend"]), {}) is None


@patch("agents.resume_selector.generate_structured")
def test_select_resume_with_llm(mock_generate) -> None:
    mock_generate.return_value = ResumeSelection(
        variant=ResumeVariant.GENERAL,
        grade="B",
        fit_score=78,
        strengths=["Linux"],
        gaps=["Rust"],
        talking_points=["eBPF work"],
    )
    jd = _jd(["systems"])
    result = select_resume(jd, None)
    assert result.variant == ResumeVariant.SYSTEMS
    assert result.grade == "B"
