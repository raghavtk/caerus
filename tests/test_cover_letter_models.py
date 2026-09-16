from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.models import (
    ApplicationPackage,
    CoverLetter,
    CoverLetterCritique,
    CoverLetterOptimizationDiagnostics,
    CoverLetterRecipient,
    CoverLetterRenderDiagnostics,
    ParsedJD,
)


def _critique(**overrides: object) -> CoverLetterCritique:
    values: dict[str, object] = {
        "approved": True,
        "factual_grounding": 5,
        "role_fit": 5,
        "company_specificity": 5,
        "voice": 5,
        "clarity": 5,
        "repetition": 5,
        "cliches": 5,
        "professional_tone": 5,
    }
    values.update(overrides)
    return CoverLetterCritique(**values)


def test_critique_scores_are_bounded() -> None:
    with pytest.raises(ValidationError):
        _critique(clarity=6)


def test_diagnostics_and_docx_path_are_public_package_fields() -> None:
    optimization = CoverLetterOptimizationDiagnostics(
        revision_count=2,
        critic=_critique(findings=["Tighten the closing."]),
        validation_codes=[],
        approved=True,
    )
    render = CoverLetterRenderDiagnostics(stage="validated", success=True, pdf_page_count=1)
    package = ApplicationPackage(
        jd=ParsedJD(),
        cover_letter=CoverLetter(body="Body", optimization_diagnostics=optimization),
        optimization_diagnostics=optimization,
        render_diagnostics=render,
        cover_letter_docx_path="outputs/letter.docx",
    )

    assert package.cover_letter_docx_path == "outputs/letter.docx"
    assert package.cover_letter_path is None
    assert package.cover_letter.optimization_diagnostics == optimization
    assert package.render_diagnostics.pdf_page_count == 1


def test_recipient_omits_blank_known_fields_and_normalizes_address_lines() -> None:
    recipient = CoverLetterRecipient(
        name="  ",
        title=" Engineering Manager ",
        company="Acme",
        address_lines=["  123 Main St  ", "", 17],
    )

    assert recipient.name is None
    assert recipient.title == "Engineering Manager"
    assert recipient.address_lines == ["123 Main St"]
