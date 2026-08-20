from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

from agents.company_research import (
    _ResearchDraft,
    _build_search_queries,
    _fit_score,
    _normalize_sources,
    _prompt_payload,
    _repair_payload,
    research_company,
)
from schemas.models import EvidenceClaim, ParsedJD, ResearchSource, ResearchStatus


def test_queries_are_current_year_role_specific() -> None:
    jd = ParsedJD(company="Acme", role="Platform Engineer", domain_signals=["systems"])
    with patch("agents.company_research.date") as mock_date:
        mock_date.today.return_value = date(2031, 2, 3)
        queries = _build_search_queries("Acme", "Platform Engineer", jd)

    assert len(queries) == 5
    assert any("2031" in query for _, query in queries)
    assert any("Platform Engineer" in query and "systems" in query for _, query in queries)


def test_source_normalization_is_bounded_canonical_and_deterministic() -> None:
    tagged = [
        ("tech", {"title": "One", "url": "HTTPS://Example.com/a/?utm_source=x#part", "snippet": "A"}),
        ("culture", {"title": "One dup", "url": "https://example.com/a", "snippet": "A"}),
        ("tech", {"title": "Two", "url": "https://example.com/b?keep=1&gclid=x", "snippet": "B"}),
        ("tech", {"title": "Capped", "url": "https://example.com/c", "snippet": "C"}),
        ("bad", {"title": "Bad", "url": "javascript:alert(1)", "snippet": "bad"}),
        ("blank", {"title": "", "url": "https://other.test/blank", "snippet": ""}),
    ]

    sources = _normalize_sources(tagged)

    assert [source.id for source in sources] == ["S1", "S2"]
    assert sources[0].url == "https://example.com/a"
    assert sources[0].query_tags == ["tech", "culture"]
    assert sources[1].url == "https://example.com/b?keep=1"


def test_source_normalization_caps_total_sources() -> None:
    tagged = [
        ("tech", {"title": f"Source {index}", "url": f"https://d{index}.test/a", "snippet": "x"})
        for index in range(12)
    ]

    sources = _normalize_sources(tagged)

    assert len(sources) == 10
    assert [source.id for source in sources] == [f"S{index}" for index in range(1, 11)]


def test_equivalent_urls_and_sibling_subdomains_share_dedupe_cap() -> None:
    tagged = [
        ("a", {"title": "One", "url": "https://a.example.com:443/p?b=2&a=1", "snippet": "x"}),
        ("b", {"title": "Duplicate", "url": "https://a.example.com/p?a=1&b=2", "snippet": "x"}),
        ("c", {"title": "Two", "url": "https://b.example.com/p", "snippet": "x"}),
        ("d", {"title": "Capped", "url": "https://c.example.com/p", "snippet": "x"}),
    ]

    sources = _normalize_sources(tagged)

    assert len(sources) == 2
    assert sources[0].url == "https://a.example.com/p?a=1&b=2"
    assert sources[0].query_tags == ["a", "b"]


def test_oversized_prompt_remains_complete_valid_json() -> None:
    jd = ParsedJD(
        company="Acme" * 100,
        role="Engineer" * 100,
        requirements=["x" * 10000] * 10,
    )
    sources = [
        ResearchSource(id="S1", title="t" * 1000, url="https://acme.test", snippet="s" * 10000)
    ]

    prompt = _prompt_payload(jd, sources, {"projects": []})
    payload = json.loads(prompt)

    assert payload["sources"][0]["id"] == "S1"
    assert len(payload["job"]["requirements"][0]) == 300
    assert len(payload["sources"][0]["snippet"]) == 600


def test_candidate_fields_cannot_make_prompt_unbounded() -> None:
    prompt = _prompt_payload(
        ParsedJD(company="Acme", role="Engineer"),
        [ResearchSource(id="S1", title="Evidence", url="https://acme.test", snippet="x")],
        {"languages": ["x" * 1_000_000], "domains": ["y" * 1_000_000], "projects": []},
    )

    assert len(prompt) < 24000
    assert json.loads(prompt)["sources"][0]["id"] == "S1"


def test_repair_payload_is_bounded_valid_json_for_large_scalar() -> None:
    evidence_prompt = _prompt_payload(
        ParsedJD(company="Acme", role="Engineer"),
        [ResearchSource(id="S1", title="Evidence", url="https://acme.test", snippet="x")],
        {"projects": []},
    )

    repair = _repair_payload(
        evidence_prompt,
        _ResearchDraft(sponsorship="x" * 1_000_000),
        ["missing citation"],
    )

    assert len(repair) < 30000
    assert len(json.loads(repair)["draft"]["sponsorship"]) == 500


@patch("agents.company_research.generate_structured")
@patch("agents.company_research.web_search", return_value=[])
def test_no_evidence_skips_llm(mock_search, mock_generate) -> None:
    brief = research_company(ParsedJD(company="Acme", role="Engineer"))

    mock_generate.assert_not_called()
    assert mock_search.call_count == 5
    assert brief.research_status == ResearchStatus.UNAVAILABLE
    assert brief.fit_score == 0
    assert not brief.sources
    assert "unavailable evidence" in " ".join(brief.concerns_or_unknowns)


@patch("agents.company_research.get_user_profile", return_value={"projects": []})
@patch("agents.company_research.generate_structured")
@patch("agents.company_research.web_search")
def test_partial_search_and_invalid_citation_are_repaired_once(mock_search, mock_generate, mock_profile) -> None:
    mock_search.side_effect = [
        RuntimeError("provider down"),
        [{"title": "Engineering", "url": "https://acme.test/eng", "snippet": "Python platform team"}],
        [],
        [],
        [],
    ]
    invalid = _ResearchDraft(
        tech_highlights=["Python platform team"],
        evidence=[EvidenceClaim(category="tech", statement="Python platform team", source_ids=["S9"])],
    )
    repaired = _ResearchDraft(
        role_context=["Platform engineers use Python"],
        tech_highlights=["Python platform team"],
        strong_overlaps=["Python"],
        evidence=[
            EvidenceClaim(category="role_context", statement="Platform engineers use Python", source_ids=["S1"]),
            EvidenceClaim(category="tech", statement="Python platform team", source_ids=["S1"]),
        ],
    )
    mock_generate.side_effect = [invalid, repaired]

    brief = research_company(
        ParsedJD(company="Acme", role="Platform Engineer", requirements=["Python"])
    )

    assert mock_generate.call_count == 2
    assert brief.research_status == ResearchStatus.PARTIAL
    assert brief.tech_highlights == ["Python platform team"]
    assert all(source_id == "S1" for claim in brief.evidence for source_id in claim.source_ids)
    assert "Search unavailable for product_engineering." in brief.concerns_or_unknowns
    assert 0 <= brief.fit_score <= 100


@patch("agents.company_research.get_user_profile", return_value={"projects": []})
@patch("agents.company_research.generate_structured")
@patch("agents.company_research.web_search")
def test_failed_repair_returns_safe_partial(mock_search, mock_generate, mock_profile) -> None:
    mock_search.return_value = [
        {"title": "Engineering", "url": "https://acme.test/eng", "snippet": "Evidence"}
    ]
    mock_generate.side_effect = [
        _ResearchDraft(
            culture_notes=["Unsupported culture claim"],
            evidence=[EvidenceClaim(category="culture", statement="Unsupported culture claim", source_ids=["S99"])],
        ),
        RuntimeError("repair unavailable"),
    ]

    brief = research_company(ParsedJD(company="Acme", role="Engineer"))

    assert brief.research_status == ResearchStatus.PARTIAL
    assert brief.culture_notes == []
    assert brief.evidence == []
    assert any("omitted" in item for item in brief.concerns_or_unknowns)


@patch("agents.company_research.get_user_profile", return_value={"projects": []})
@patch("agents.company_research.generate_structured")
@patch("agents.company_research.web_search")
def test_complete_status_requires_valid_core_evidence_and_no_search_failures(
    mock_search, mock_generate, mock_profile
) -> None:
    mock_search.return_value = [
        {"title": "Engineering", "url": "https://acme.test/eng", "snippet": "Python platform"}
    ]
    mock_generate.return_value = _ResearchDraft(
        role_context=["Platform engineers build Python services"],
        tech_highlights=["The platform uses Python"],
        evidence=[
            EvidenceClaim(
                category="role_context",
                statement="Platform engineers build Python services",
                source_ids=["S1"],
            ),
            EvidenceClaim(category="tech", statement="The platform uses Python", source_ids=["S1"]),
        ],
    )

    brief = research_company(ParsedJD(company="Acme", role="Platform Engineer"))

    assert brief.research_status == ResearchStatus.COMPLETE
    assert mock_generate.call_count == 1


@patch("agents.company_research.get_user_profile", return_value={"projects": []})
@patch("agents.company_research.generate_structured")
@patch("agents.company_research.web_search")
def test_uncited_downstream_fields_are_removed(mock_search, mock_generate, mock_profile) -> None:
    mock_search.return_value = [
        {"title": "Engineering", "url": "https://acme.test/eng", "snippet": "Python platform"}
    ]
    mock_generate.return_value = _ResearchDraft(
        strong_overlaps=["Unsupported overlap"],
        potential_angles=["Unsupported angle"],
        candidate_overlaps=["Unsupported candidate claim"],
        talking_points=["Unsupported talking point"],
    )

    brief = research_company(ParsedJD(company="Acme", role="Engineer"))

    assert brief.strong_overlaps == []
    assert brief.potential_angles == []
    assert brief.candidate_overlaps == []
    assert brief.talking_points == []
    assert any("omitted" in item for item in brief.concerns_or_unknowns)


def test_fit_score_does_not_reward_uncited_model_overlap_counts() -> None:
    jd = ParsedJD(role="Python Engineer", requirements=["Python"])
    claims = [EvidenceClaim(category="tech", statement="Python services", source_ids=["S1"])]
    baseline = _fit_score(jd, _ResearchDraft(), {"projects": []}, claims)
    inflated = _fit_score(
        jd,
        _ResearchDraft(strong_overlaps=["arbitrary"] * 3, candidate_overlaps=["arbitrary"] * 3),
        {"projects": []},
        claims,
    )

    assert inflated == baseline


@patch("agents.company_research.get_user_profile", return_value={"projects": []})
@patch("agents.company_research.generate_structured")
@patch("agents.company_research.web_search")
def test_orphan_claims_cannot_make_research_complete_or_inflate_fit(
    mock_search, mock_generate, mock_profile
) -> None:
    mock_search.return_value = [
        {"title": "Engineering", "url": "https://acme.test/eng", "snippet": "Python Engineer"}
    ]
    mock_generate.return_value = _ResearchDraft(
        evidence=[
            EvidenceClaim(category="role_context", statement="Python Engineer", source_ids=["S1"]),
            EvidenceClaim(category="tech", statement="Python", source_ids=["S1"]),
        ]
    )

    brief = research_company(
        ParsedJD(company="Acme", role="Python Engineer", requirements=["Python"])
    )

    assert brief.research_status == ResearchStatus.PARTIAL
    assert brief.role_context == []
    assert brief.tech_highlights == []
    assert brief.evidence == []
    assert brief.fit_score == 0
