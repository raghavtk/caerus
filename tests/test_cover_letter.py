from __future__ import annotations

from unittest.mock import patch

import pytest

from agents.cover_letter import (
    CoverLetterQualityError,
    _build_user_prompt,
    count_words,
    generate_cover_letter,
    normalize_cover_letter,
    validate_cover_letter,
)
from schemas.models import CompanyBrief, CompanyStage, ParsedJD, ResumeSelection, ResumeVariant


def _inputs() -> tuple[ParsedJD, CompanyBrief, ResumeSelection, dict]:
    profile = {
        "name": "Candidate",
        "voice_profile": {"tone": "clear, direct, grounded", "forbidden_phrases": ["synergy wizard"]},
        "experience": [{"company": "Example Labs", "title": "Software Engineering Intern", "bullets": ["Built APIs"]}],
        "projects": [
            {
                "name": "Caerus",
                "tier": "A",
                "include_in_cover_letter": True,
                "stack": ["Python"],
                "description": "A job application agent.",
            }
        ],
    }
    return (
        ParsedJD(company="Acme", role="Software Engineer", requirements=["Python"]),
        CompanyBrief(company="Acme", stage=CompanyStage.GROWTH, tech_highlights=["Python"]),
        ResumeSelection(variant=ResumeVariant.AI_ML, grade="A"),
        profile,
    )


def _paragraph(seed: str, target: int = 60) -> str:
    words = seed.split()
    words.extend(["evidence"] * (target - count_words(seed)))
    return " ".join(words)


def _valid_body() -> str:
    return "\n\n".join(
        [
            _paragraph("Acme's Software Engineer role connects directly with my work on Caerus."),
            _paragraph("Caerus gave me practical Python experience grounded in careful engineering decisions."),
            _paragraph("The Software Engineer opportunity at Acme offers a useful place to contribute that experience."),
        ]
    )


def _violations(body: str, profile: dict | None = None) -> list[str]:
    jd, _, _, default_profile = _inputs()
    return validate_cover_letter(body, jd=jd, profile=profile or default_profile)


def test_valid_cover_letter_passes_contract() -> None:
    assert count_words(_valid_body()) == 180
    assert _violations(_valid_body()) == []


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("short\n\nletter\n\nCaerus Acme Software Engineer", "180-260 words"),
        (_valid_body().replace("\n\n", "\n", 1), "exactly 3"),
        (_valid_body().replace("connects", "connects — clearly"), "em and en dashes"),
        (_valid_body().replace("Caerus gave", "- Caerus gave"), "bullets"),
        (_valid_body().replace("Caerus gave", "+ Caerus gave"), "bullets"),
        (_valid_body().replace("Acme's", "Subject: Application\nAcme's"), "markup"),
        (_valid_body().replace("connects directly", "connects **directly**"), "markup"),
        (_valid_body().replace("connects directly", "connects _directly_"), "markup"),
        (_valid_body().replace("connects directly", "connects `directly`"), "markup"),
        (_valid_body().replace("Acme's", "> Acme's"), "markup"),
        ("I " + _valid_body(), "begin the opening"),
        ("“I " + _valid_body(), "begin the opening"),
        (_valid_body().replace("connects directly", "is a perfect fit and connects directly"), "generic phrases"),
        (_valid_body().replace("Acme's", "OtherCo's").replace("at Acme", "at OtherCo"), "company name"),
        (_valid_body().replace("Software Engineer", "Developer"), "role title"),
        (_valid_body().replace("Caerus", "another project"), "identifier exactly"),
    ],
)
def test_validator_reports_objective_rule(body: str, message: str) -> None:
    assert any(message in violation for violation in _violations(body))


def test_validator_applies_profile_forbidden_phrases() -> None:
    body = _valid_body().replace("connects directly", "shows synergy wizard thinking and connects directly")
    assert any("synergy wizard" in violation for violation in _violations(body))


def test_validator_reports_missing_profile_evidence() -> None:
    jd, _, _, profile = _inputs()
    profile["projects"] = []
    profile["experience"] = []
    violations = validate_cover_letter(_valid_body(), jd=jd, profile=profile)
    assert any("usable project or experience" in violation for violation in violations)


def test_grounding_requires_phrase_boundaries() -> None:
    body = _valid_body().replace("Acme's", "Acmeology's").replace("at Acme", "at Acmeology")
    body = body.replace("Software Engineer", "Software Engineerings").replace("Caerus", "Caeruses")
    violations = _violations(body)
    assert any("company name" in violation for violation in violations)
    assert any("role title" in violation for violation in violations)
    assert any("identifier exactly" in violation for violation in violations)


@patch("config.get_user_profile")
def test_explicit_empty_profile_does_not_reload_private_profile(mock_get_profile) -> None:
    jd, _, _, _ = _inputs()
    violations = validate_cover_letter(_valid_body(), jd=jd, profile={})
    mock_get_profile.assert_not_called()
    assert any("usable project or experience" in violation for violation in violations)


def test_normalization_and_word_count_are_shared() -> None:
    body = "Acme's well-tested work.\r\n\r\nSecond paragraph.\r\n\r\nThird paragraph."
    assert normalize_cover_letter(body) == "Acme's well-tested work.\n\nSecond paragraph.\n\nThird paragraph."
    assert count_words(body) == 8


def test_prompt_uses_bounded_grounded_context() -> None:
    jd, brief, selection, profile = _inputs()
    jd.requirements = [f"required-{index}" for index in range(10)]
    prompt = _build_user_prompt(jd, brief, selection, profile)
    assert "required-7" in prompt
    assert "required-8" not in prompt
    assert "Caerus" in prompt
    assert "Example Labs" in prompt
    assert "CompanyStage.GROWTH" not in prompt


@patch("agents.cover_letter.get_user_profile")
@patch("agents.cover_letter.generate_text")
def test_generate_cover_letter_repairs_once_and_derives_hook(mock_generate, mock_profile) -> None:
    jd, brief, selection, profile = _inputs()
    mock_profile.return_value = profile
    mock_generate.side_effect = ["Too short.", _valid_body()]

    result = generate_cover_letter(jd, brief, selection)

    assert mock_generate.call_count == 2
    assert "VIOLATIONS:" in mock_generate.call_args_list[1].kwargs["user_prompt"]
    assert "ORIGINAL EVIDENCE:" in mock_generate.call_args_list[1].kwargs["user_prompt"]
    assert mock_generate.call_args_list[0].kwargs["trace_content"] is False
    assert result.word_count == 180
    assert result.hook_summary.startswith("Acme's Software Engineer role")


@patch("agents.cover_letter.get_user_profile")
@patch("agents.cover_letter.generate_text", return_value="Still invalid.")
def test_generate_cover_letter_fails_after_one_repair(mock_generate, mock_profile) -> None:
    _, _, _, profile = _inputs()
    mock_profile.return_value = profile
    jd, brief, selection, _ = _inputs()

    with pytest.raises(CoverLetterQualityError) as exc_info:
        generate_cover_letter(jd, brief, selection)

    assert mock_generate.call_count == 2
    assert exc_info.value.violations
