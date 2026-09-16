from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from schemas.models import (
    ApplicationPackage,
    ApplicationStatus,
    CompanyBrief,
    CoverLetter,
    CoverLetterRecipient,
    EvidenceClaim,
    ParsedJD,
    ProjectRecommendation,
    ResearchSource,
    ResearchStatus,
    ResumeSelection,
)
from skills.output_writer import (
    OutputWriteError,
    _convert_docx_to_pdf,
    _output_name,
    _write_company_brief,
    _write_cover_letter_docx,
    _write_resume_report,
    write_outputs,
)


def test_resume_report_renders_project_recommendations(tmp_path) -> None:
    selection = ResumeSelection(
        project_recommendations=[
            ProjectRecommendation(
                project_id="caerus",
                name="Caerus",
                repository_url="https://github.com/example/caerus",
                source_ref="main",
                score=88,
                technology_score=35,
                domain_score=20,
                company_role_score=15,
                tier_score=10,
                recency_score=5,
                reason="Matched required: Python",
                matched_required=["Python"],
            )
        ]
    )
    package = ApplicationPackage(jd=ParsedJD(), resume_selection=selection)

    path = _write_resume_report(package, tmp_path)
    report = path.read_text(encoding="utf-8")

    assert "### Caerus (88/100)" in report
    assert "technology 35/40" in report
    assert "https://github.com/example/caerus" in report


def test_resume_report_handles_empty_recommendations(tmp_path) -> None:
    package = ApplicationPackage(jd=ParsedJD(), resume_selection=ResumeSelection())
    report = _write_resume_report(package, tmp_path).read_text(encoding="utf-8")
    assert "## Recommended Projects" in report


def test_company_brief_renders_status_evidence_and_sources(tmp_path) -> None:
    brief = CompanyBrief(
        company="Acme",
        research_status=ResearchStatus.PARTIAL,
        concerns_or_unknowns=["Sponsorship unknown"],
        evidence=[EvidenceClaim(category="tech", statement="Uses Python", source_ids=["S1"])],
        sources=[ResearchSource(id="S1", title="Engineering", url="https://acme.test/eng")],
    )
    package = ApplicationPackage(jd=ParsedJD(), company_brief=brief)

    report = _write_company_brief(package, tmp_path).read_text(encoding="utf-8")

    assert "Research Status: partial" in report
    assert "Uses Python [S1]" in report
    assert "S1 — Engineering — https://acme.test/eng" in report
    assert "## Role Context\n- Unknown" in report


def _complete_package() -> ApplicationPackage:
    return ApplicationPackage(
        jd=ParsedJD(company="Acme", role="Engineer"),
        company_brief=CompanyBrief(company="Acme"),
        resume_selection=ResumeSelection(),
    )


def test_output_name_includes_session_suffix() -> None:
    first = _output_name("Acme", "Engineer", "session-one")
    second = _output_name("Acme", "Engineer", "session-two")
    assert first != second
    assert first.endswith("session_one")


@patch("skills.output_writer.find_libreoffice", return_value="soffice.com")
@patch("skills.output_writer.subprocess.run")
@patch("skills.output_writer.tempfile.TemporaryDirectory")
def test_pdf_conversion_uses_short_external_libreoffice_profile(
    mock_tempdir, mock_run, mock_find, tmp_path
) -> None:
    staging = tmp_path / ("long-staging-name-" * 6)
    staging.mkdir()
    docx_path = staging / "cover_letter.docx"
    docx_path.write_bytes(b"docx")
    short_profile = tmp_path / "lo-profile"
    mock_tempdir.return_value.__enter__.return_value = str(short_profile)

    def complete_conversion(command, **kwargs):
        (staging / "cover_letter.pdf").write_bytes(b"pdf")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    mock_run.side_effect = complete_conversion

    result = _convert_docx_to_pdf(docx_path, staging)

    assert result == staging / "cover_letter.pdf"
    command = mock_run.call_args.args[0]
    profile_arg = next(arg for arg in command if arg.startswith("-env:UserInstallation="))
    assert str(staging) not in profile_arg


@patch("skills.output_writer.get_settings")
@patch("skills.output_writer.get_user_profile", return_value={"name": "Avery O'Neil"})
def test_cover_letter_docx_renders_known_contact_and_recipient_fields(
    mock_profile, mock_settings, tmp_path
) -> None:
    Document = pytest.importorskip("docx").Document

    mock_settings.return_value = SimpleNamespace(
        cover_letter_email="avery@example.com",
        cover_letter_phone=None,
        cover_letter_location="New York, NY",
        cover_letter_linkedin_url="https://linkedin.com/in/avery",
        cover_letter_portfolio_url=None,
    )
    body = "Opening paragraph.\n\nEvidence paragraph.\n\nClosing paragraph."
    package = ApplicationPackage(
        jd=ParsedJD(company="Acme", role="Engineer"),
        cover_letter=CoverLetter(body=body),
        cover_letter_recipient=CoverLetterRecipient(title="Engineering Manager", company="Acme"),
    )

    path = _write_cover_letter_docx(package, tmp_path)
    paragraphs = [paragraph.text for paragraph in Document(path).paragraphs]

    assert path.name == "cover_letter.docx"
    assert "avery@example.com | New York, NY" in paragraphs
    assert "https://linkedin.com/in/avery" in paragraphs
    assert "Engineering Manager" in paragraphs
    assert "Acme" in paragraphs
    assert paragraphs[-2:] == ["Sincerely,", "Avery O'Neil"]
    assert "Hiring Manager" not in paragraphs
    assert paragraphs.count("Opening paragraph.") == 1


@patch("skills.output_writer.get_settings")
@patch("skills.output_writer.get_user_profile", return_value={})
def test_cover_letter_docx_rejects_missing_candidate_name(mock_profile, mock_settings, tmp_path) -> None:
    pytest.importorskip("docx")
    mock_settings.return_value = SimpleNamespace(
        cover_letter_email=None,
        cover_letter_phone=None,
        cover_letter_location=None,
        cover_letter_linkedin_url=None,
        cover_letter_portfolio_url=None,
    )
    package = ApplicationPackage(
        jd=ParsedJD(company="Acme", role="Engineer"),
        cover_letter=CoverLetter(body="One.\n\nTwo.\n\nThree."),
    )
    with pytest.raises(ValueError, match="candidate name is required"):
        _write_cover_letter_docx(package, tmp_path)


@patch("skills.output_writer._validate_cover_letter_artifacts", return_value=1)
@patch("skills.output_writer._convert_docx_to_pdf")
@patch("skills.output_writer._write_cover_letter_docx")
@patch("skills.output_writer.get_settings")
def test_write_outputs_publishes_complete_directory_atomically(
    mock_settings, mock_docx, mock_pdf, mock_validate, tmp_path
) -> None:
    mock_settings.return_value = SimpleNamespace(outputs_dir=str(tmp_path))
    mock_docx.side_effect = lambda package, out: (out / "cover_letter.docx")
    mock_pdf.side_effect = lambda docx, out: (out / "cover_letter.pdf")
    package = _complete_package()
    package.session_id = "stable-session"

    result = write_outputs(package)

    assert Path(result.output_dir).is_dir()
    assert result.cover_letter_docx_path.endswith("cover_letter.docx")
    assert result.cover_letter_path.endswith("cover_letter.pdf")
    assert result.render_diagnostics.success is True
    assert not list((tmp_path / ".diagnostics").glob("*.staging-*"))


@patch("skills.output_writer._write_cover_letter_docx", side_effect=ValueError("bad body"))
@patch("skills.output_writer.get_settings")
def test_write_outputs_retains_diagnostics_without_publishing(mock_settings, mock_docx, tmp_path) -> None:
    mock_settings.return_value = SimpleNamespace(outputs_dir=str(tmp_path))
    package = _complete_package()

    with pytest.raises(OutputWriteError) as exc_info:
        write_outputs(package)

    assert exc_info.value.stage == "docx_generation"
    assert package.output_dir is None
    assert package.status is ApplicationStatus.CREATED
    assert package.render_diagnostics.success is False
    assert Path(package.render_diagnostics.diagnostics_path).is_dir()
