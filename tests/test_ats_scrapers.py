from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from skills.ats_scrapers.common import FetchedJobPosting
from skills.ats_scrapers.greenhouse import (
    _fetch_html as greenhouse_fetch_html,
    _posting_from_api as greenhouse_from_api,
    fetch_greenhouse_posting,
    parse_greenhouse_url,
)
from skills.ats_scrapers.lever import (
    _fetch_html as lever_fetch_html,
    _posting_from_api as lever_from_api,
    fetch_lever_posting,
    parse_lever_url,
)


FIXTURES = Path(__file__).parent / "fixtures" / "ats"


class _FakeResponse:
    def __init__(self, *, text: str = "", payload: dict[str, object] | None = None) -> None:
        self.text = text
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        assert self._payload is not None
        return self._payload


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def get(self, _: str) -> _FakeResponse:
        return self.response


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://boards.greenhouse.io/acme/jobs/123?gh_jid=123#apply", ("acme", "123")),
        ("https://job-boards.greenhouse.io/acme/jobs/456/", ("acme", "456")),
    ],
)
def test_parse_greenhouse_url_variants(url: str, expected: tuple[str, str]) -> None:
    assert parse_greenhouse_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://boards.greenhouse.io.example.com/acme/jobs/123",
        "https://boards.greenhouse.io/acme/jobs/not-a-number",
        "https://boards.greenhouse.io/acme/jobs",
    ],
)
def test_parse_greenhouse_url_rejects_invalid_hosts_and_paths(url: str) -> None:
    assert parse_greenhouse_url(url) is None
    assert parse_greenhouse_url(url.replace("https://", "ftp://")) is None


@pytest.mark.parametrize(
    ("url", "expected_api"),
    [
        ("https://jobs.lever.co/acme/abc123/apply?x=1", "https://api.lever.co"),
        ("https://jobs.eu.lever.co/acme/abc123/#apply", "https://api.eu.lever.co"),
    ],
)
def test_parse_lever_url_variants(url: str, expected_api: str) -> None:
    parsed = parse_lever_url(url)
    assert parsed is not None
    assert parsed[:2] == ("acme", "abc123")
    assert parsed[2] == expected_api


def test_parse_lever_url_rejects_spoofed_host() -> None:
    assert parse_lever_url("https://jobs.lever.co.example.com/acme/abc123") is None
    assert parse_lever_url("ftp://jobs.lever.co/acme/abc123") is None


def test_greenhouse_api_normalizes_metadata_and_content() -> None:
    posting = greenhouse_from_api(
        "https://boards.greenhouse.io/acme/jobs/1234567",
        "acme",
        "1234567",
        _fixture("greenhouse_posting.json"),
    )
    assert posting.fetch_source == "greenhouse_api"
    assert "Company: Example Systems" in posting.raw_text
    assert "Department: Engineering" in posting.raw_text
    assert "Compensation: Base salary: USD 145,000 - USD 185,000" in posting.raw_text
    assert "About the role" in posting.raw_text
    assert "- Design Linux services" in posting.raw_text
    assert "RETAIN-ME-LATE-REQUIREMENT" in posting.raw_text
    assert posting.raw_text.index("What you will do") < posting.raw_text.index("Requirements")


def test_lever_api_normalizes_all_sections_without_duplicate_opening() -> None:
    posting = lever_from_api(
        "https://jobs.lever.co/acme/abc123",
        "abc123",
        _fixture("lever_posting.json"),
    )
    assert posting.fetch_source == "lever_api"
    assert "Team: Data Platform" in posting.raw_text
    assert "Workplace Type: remote" in posting.raw_text
    assert "Compensation: $160,000 - $200,000 USD" in posting.raw_text
    assert "Compensation: USD 160,000 - 200,000 per year" in posting.raw_text
    assert posting.raw_text.count("$160,000 - $200,000 USD") == 1
    assert posting.raw_text.count("Build dependable storage systems") == 1
    assert posting.raw_text.index("Requirements") < posting.raw_text.index("Nice to have")
    assert "RETAIN-ME-LATE-REQUIREMENT" in posting.raw_text


def test_greenhouse_html_fixture_excludes_form_content() -> None:
    response = _FakeResponse(text=(FIXTURES / "greenhouse_posting.html").read_text())
    with patch("skills.ats_scrapers.greenhouse.httpx.Client", return_value=_FakeClient(response)):
        posting = greenhouse_fetch_html("https://boards.greenhouse.io/acme/jobs/123", "123")
    assert posting.fetch_source == "greenhouse_html"
    assert "Health coverage" in posting.raw_text
    assert "Demographic questions" not in posting.raw_text


def test_lever_html_fixture_preserves_sections_and_excludes_form_content() -> None:
    response = _FakeResponse(text=(FIXTURES / "lever_posting.html").read_text())
    with patch("skills.ats_scrapers.lever.httpx.Client", return_value=_FakeClient(response)):
        posting = lever_fetch_html("https://jobs.lever.co/acme/abc123", "abc123")
    assert posting.fetch_source == "lever_html"
    assert "Requirements" in posting.raw_text
    assert "- PostgreSQL" in posting.raw_text
    assert "Consent field" not in posting.raw_text


def test_greenhouse_uses_html_when_api_fails() -> None:
    expected = FetchedJobPosting("full content " * 20, "Greenhouse", "url", "greenhouse_html")
    with patch("skills.ats_scrapers.greenhouse._fetch_api", side_effect=RuntimeError("down")), patch(
        "skills.ats_scrapers.greenhouse._fetch_html", return_value=expected
    ) as html:
        assert fetch_greenhouse_posting("https://boards.greenhouse.io/acme/jobs/123") is expected
    html.assert_called_once()


def test_lever_uses_html_when_api_is_unusable() -> None:
    expected = FetchedJobPosting("full content " * 20, "Lever", "url", "lever_html")
    with patch("skills.ats_scrapers.lever._fetch_api", side_effect=ValueError("empty")), patch(
        "skills.ats_scrapers.lever._fetch_html", return_value=expected
    ) as html:
        assert fetch_lever_posting("https://jobs.lever.co/acme/abc123") is expected
    html.assert_called_once()


def test_provider_raises_after_api_and_html_fail() -> None:
    with patch("skills.ats_scrapers.greenhouse._fetch_api", side_effect=RuntimeError("api")), patch(
        "skills.ats_scrapers.greenhouse._fetch_html", side_effect=RuntimeError("html")
    ):
        with pytest.raises(RuntimeError, match="html"):
            fetch_greenhouse_posting("https://boards.greenhouse.io/acme/jobs/123")
