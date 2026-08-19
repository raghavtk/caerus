from __future__ import annotations

from schemas.models import ApplicationPackage, ParsedJD, ProjectRecommendation, ResumeSelection
from skills.output_writer import _write_resume_report


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
