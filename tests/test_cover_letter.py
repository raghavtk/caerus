from __future__ import annotations

from unittest.mock import patch

from agents.cover_letter import _build_user_prompt, generate_cover_letter
from schemas.models import CompanyBrief, CompanyStage, ParsedJD, ResumeSelection, ResumeVariant


def _inputs() -> tuple[ParsedJD, CompanyBrief, ResumeSelection]:
    return (
        ParsedJD(company="Acme", role="Engineer", requirements=["Python"]),
        CompanyBrief(company="Acme", stage=CompanyStage.GROWTH),
        ResumeSelection(variant=ResumeVariant.AI_ML, grade="A"),
    )


def test_cover_letter_prompt_uses_enum_values() -> None:
    jd, brief, selection = _inputs()
    prompt = _build_user_prompt(jd, brief, selection, {})

    assert "'stage': 'Growth'" in prompt
    assert "'variant': 'AI_ML'" in prompt
    assert "CompanyStage.GROWTH" not in prompt
    assert "ResumeVariant.AI_ML" not in prompt


@patch("agents.cover_letter.get_user_profile", return_value={})
@patch("agents.cover_letter.generate_structured", side_effect=ValueError("invalid summary"))
@patch("agents.cover_letter.generate_text", return_value="A specific opening.\n\nFit paragraph.\n\nClosing paragraph.")
def test_cover_letter_uses_bounded_text_budget_and_hook_fallback(mock_text, mock_summary, mock_profile) -> None:
    jd, brief, selection = _inputs()
    result = generate_cover_letter(jd, brief, selection)

    assert mock_text.call_args.kwargs["max_tokens"] == 1024
    assert result.hook_summary == "A specific opening."
