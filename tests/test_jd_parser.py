from __future__ import annotations

from unittest.mock import patch

from agents.jd_parser import _detect_ats, parse_jd
from schemas.models import ParsedJD


def test_detect_ats_variants() -> None:
    assert _detect_ats("https://boards.greenhouse.io/acme/jobs/1") == "Greenhouse"
    assert _detect_ats("https://jobs.lever.co/acme/123") == "Lever"
    assert _detect_ats("https://mycompany.workdayjobs.com/en-US/careers/job/abc") == "Workday"
    assert _detect_ats("https://careers.taleo.net/careersection/jobdetail.ftl") == "Taleo"
    assert _detect_ats("https://www.linkedin.com/jobs/view/123") == "LinkedIn"


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
