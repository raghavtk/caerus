from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from agents.resume_selector import _heuristic_select, rank_projects, select_resume
from schemas.models import CompanyBrief, ParsedJD, ProjectRecommendation, ResumeSelection, ResumeVariant


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


@patch("agents.resume_selector.get_settings", return_value=SimpleNamespace(resumes_dir="resumes"))
@patch("agents.resume_selector.get_user_profile", return_value={"projects": []})
@patch("agents.resume_selector.generate_structured")
def test_select_resume_with_llm(mock_generate, mock_profile, mock_settings) -> None:
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


def _full_match_project(**overrides) -> dict:
    project = {
        "id": "full-match",
        "name": "Full Match",
        "repository_url": "https://github.com/example/full-match",
        "source_ref": "main",
        "stack": ["Python", "Kubernetes", "Go"],
        "domains": ["systems", "backend"],
        "company_signals": ["Acme", "cloud"],
        "role_signals": ["Software Engineer"],
        "tier": "A",
        "end_date": "present",
        "resume_eligible": True,
    }
    project.update(overrides)
    return project


def _scoring_inputs() -> tuple[ParsedJD, CompanyBrief]:
    return (
        ParsedJD(
            company="Acme",
            role="Software Engineer",
            requirements=["Python", "Kubernetes"],
            preferred=["Go"],
            domain_signals=["systems", "backend"],
        ),
        CompanyBrief(company="Acme", tech_highlights=["cloud"]),
    )


def test_rank_projects_exact_full_score_and_breakdown() -> None:
    jd, brief = _scoring_inputs()
    result = rank_projects({"projects": [_full_match_project()]}, jd, brief, today=date(2026, 8, 19))

    assert len(result) == 1
    match = result[0]
    assert match.score == 100
    assert (match.technology_score, match.domain_score, match.company_role_score) == (40, 25, 20)
    assert (match.tier_score, match.recency_score) == (10, 5)
    assert match.matched_required == ["Python", "Kubernetes"]
    assert match.matched_preferred == ["Go"]


def test_required_and_preferred_weights_are_30_and_10() -> None:
    jd = ParsedJD(requirements=["Python"], preferred=["Go"])
    required = rank_projects(
        {"projects": [{"id": "required", "name": "Required", "stack": ["Python"]}]},
        jd,
        today=date(2026, 1, 1),
    )[0]
    preferred = rank_projects(
        {"projects": [{"id": "preferred", "name": "Preferred", "stack": ["Go"]}]},
        jd,
        today=date(2026, 1, 1),
    )[0]

    assert required.technology_score == 30
    assert preferred.technology_score == 10


def test_one_required_match_outranks_one_preferred_with_many_requirements() -> None:
    jd = ParsedJD(requirements=["Python", "Go", "Rust", "Java", "C"], preferred=["SQL"])
    result = rank_projects(
        {
            "projects": [
                {"id": "required", "name": "Required", "stack": ["Python"]},
                {"id": "preferred", "name": "Preferred", "stack": ["SQL"]},
            ]
        },
        jd,
        today=date(2026, 1, 1),
    )

    assert [item.project_id for item in result] == ["required", "preferred"]


def test_normalization_aliases_and_no_substring_matches() -> None:
    alias = rank_projects(
        {"projects": [{"id": "go", "name": "Go Project", "technologies": "golang"}]},
        ParsedJD(requirements=["Go"]),
        today=date(2026, 1, 1),
    )[0]
    collision = rank_projects(
        {"projects": [{"id": "cloud", "name": "Cloud", "stack": ["Cloud"]}]},
        ParsedJD(requirements=["C"]),
        today=date(2026, 1, 1),
    )[0]

    assert alias.technology_score == 40
    assert collision.technology_score == 0


def test_recency_handles_present_dates_and_malformed_future_values() -> None:
    jd = ParsedJD()
    projects = [
        {"id": "present", "name": "Present", "dates": "2025 - Present"},
        {"id": "recent", "name": "Recent", "end_date": date(2025, 12, 1)},
        {"id": "older", "name": "Older", "dates": "2021 - 2022"},
        {"id": "future", "name": "Future", "end_date": "2030-01-01"},
        {"id": "bad", "name": "Bad", "end_date": "eventually"},
    ]
    ranked = rank_projects({"projects": projects}, jd, today=date(2026, 8, 19))
    scores = {item.project_id: item.recency_score for item in ranked}

    assert scores == {"present": 5, "recent": 5, "older": 1}


def test_top_three_exclusion_and_stable_input_ties() -> None:
    projects = [
        {"id": f"p{index}", "name": f"Project {index}", "tier": "B"}
        for index in range(5)
    ]
    projects[1]["resume_eligible"] = False
    result = rank_projects({"projects": projects}, ParsedJD(), today=date(2026, 1, 1))

    assert [item.project_id for item in result] == ["p0", "p2", "p3"]


def test_string_false_resume_eligibility_is_excluded() -> None:
    projects = [
        {"id": "excluded", "name": "Excluded", "resume_eligible": "false"},
        {"id": "included", "name": "Included"},
    ]

    result = rank_projects({"projects": projects}, ParsedJD(), today=date(2026, 1, 1))

    assert [item.project_id for item in result] == ["included"]


def test_rank_projects_skips_malformed_and_unnamed_entries() -> None:
    profile = {"projects": ["bad", None, {}, {"id": "valid", "name": "Valid", "tier": "C"}]}
    result = rank_projects(profile, ParsedJD(), today=date(2026, 1, 1))
    assert [item.project_id for item in result] == ["valid"]


@patch("agents.resume_selector.get_settings", return_value=SimpleNamespace(resumes_dir="resumes"))
@patch("agents.resume_selector.get_user_profile")
@patch("agents.resume_selector.generate_structured")
def test_llm_cannot_override_deterministic_recommendations(mock_generate, mock_profile, mock_settings) -> None:
    jd, brief = _scoring_inputs()
    profile = {"projects": [_full_match_project()], "resume_variants": {}}
    mock_profile.return_value = profile
    mock_generate.return_value = ResumeSelection(
        project_recommendations=[
            ProjectRecommendation(project_id="fake", name="Fake", score=100, reason="LLM")
        ]
    )

    result = select_resume(jd, brief)

    assert [item.project_id for item in result.project_recommendations] == ["full-match"]
    prompt = mock_generate.call_args.kwargs["user_prompt"]
    assert '"deterministic_project_ranking"' in prompt
    assert "selected_resume_path" not in prompt
    assert "https://github.com/example/full-match" not in prompt
    assert '"source_ref"' not in prompt
    assert mock_generate.call_args.kwargs["trace_content"] is False
