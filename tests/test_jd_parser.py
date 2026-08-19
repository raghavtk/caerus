from __future__ import annotations

from unittest.mock import patch

import pytest

from agents.jd_parser import _detect_ats, _fetch_job, parse_jd
from schemas.models import ParsedJD
from skills.ats_scrapers.common import FetchedJobPosting


def test_detect_ats_variants() -> None:
    assert _detect_ats("https://boards.greenhouse.io/acme/jobs/1") == "Greenhouse"
    assert _detect_ats("https://jobs.lever.co/acme/123") == "Lever"
    assert _detect_ats("https://mycompany.workdayjobs.com/en-US/careers/job/abc") == "Workday"
    assert _detect_ats("https://careers.taleo.net/careersection/jobdetail.ftl") == "Taleo"
    assert _detect_ats("https://www.linkedin.com/jobs/view/123") == "LinkedIn"
    assert _detect_ats("https://jobs.ashbyhq.com/acme/123") == "Ashby"
    assert _detect_ats("https://boards.greenhouse.io.example.com/acme/jobs/1") is None


@patch("agents.jd_parser.generate_structured")
def test_parse_jd_with_pasted_text(mock_generate) -> None:
    mock_generate.return_value = ParsedJD(
        company="Cloudflare",
        role="Software Engineer",
        location="Remote",
        seniority="0-2 yrs",
        requirements=["Go", "Linux", "eBPF"],
        preferred=["Rust"],
        domain_signals=["systems", "networking"],
        raw_text="",
    )
    input_text = "Software Engineer at Cloudflare. Requirements: Go Linux eBPF."
    result = parse_jd(input_text)
    assert result.company == "Cloudflare"
    assert result.role == "Software Engineer"
    assert "eBPF" in result.raw_text
    assert mock_generate.call_args.kwargs["max_tokens"] == 4096


@patch("agents.jd_parser.generate_structured")
def test_parse_jd_keeps_full_long_pasted_text(mock_generate) -> None:
    mock_generate.return_value = ParsedJD(raw_text="")
    long_text = "x" * 12050 + " RETAIN-ME-LATE-REQUIREMENT"
    result = parse_jd(long_text)
    prompt = mock_generate.call_args.kwargs["user_prompt"]
    assert "RETAIN-ME-LATE-REQUIREMENT" in prompt
    assert result.raw_text == long_text


@patch("agents.jd_parser.generate_structured")
@patch("agents.jd_parser._fetch_job")
def test_provider_metadata_overrides_conflicting_llm_values(mock_fetch, mock_generate) -> None:
    mock_fetch.return_value = FetchedJobPosting(
        raw_text="complete posting",
        ats="Greenhouse",
        source_url="https://boards.greenhouse.io/acme/jobs/123",
        fetch_source="greenhouse_api",
        company="Example Systems",
        role="Platform Engineer",
        location="Remote",
    )
    mock_generate.return_value = ParsedJD(company="Wrong", role="Wrong", location="Wrong", ats="Wrong")
    result = parse_jd("https://boards.greenhouse.io/acme/jobs/123")
    assert (result.company, result.role, result.location, result.ats) == (
        "Example Systems",
        "Platform Engineer",
        "Remote",
        "Greenhouse",
    )


def test_recognized_provider_failure_falls_through_to_jina() -> None:
    with patch("agents.jd_parser.fetch_greenhouse_posting", side_effect=RuntimeError("provider")), patch(
        "agents.jd_parser._fetch_via_jina", return_value="Jina posting content"
    ):
        posting = _fetch_job("https://boards.greenhouse.io/acme/jobs/123")
    assert posting.fetch_source == "jina"
    assert posting.ats == "Greenhouse"
    assert "ATS: Greenhouse" in posting.raw_text
    assert "Fetch Source: jina" in posting.raw_text


def test_unknown_url_uses_jina_only() -> None:
    with patch("agents.jd_parser._fetch_via_jina", return_value="Jina posting content") as jina:
        posting = _fetch_job("https://careers.example.com/jobs/123")
    assert posting.fetch_source == "jina"
    jina.assert_called_once()


def test_all_url_fetches_failing_raise_clear_value_error() -> None:
    with patch("agents.jd_parser.fetch_greenhouse_posting", side_effect=RuntimeError("provider")), patch(
        "agents.jd_parser._fetch_via_jina", side_effect=RuntimeError("jina")
    ):
        with pytest.raises(ValueError, match="failed to fetch job description from URL"):
            _fetch_job("https://boards.greenhouse.io/acme/jobs/123")


def test_empty_pasted_text_is_rejected() -> None:
    with pytest.raises(ValueError, match="job description content is empty"):
        parse_jd("   ")
