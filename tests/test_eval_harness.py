from __future__ import annotations

from schemas.models import CoverLetter, ParsedJD, ResumeSelection, ResumeVariant
from skills.agent_eval import (
    check_cover_letter,
    check_jd_parser,
    check_resume_selector,
    list_fixture_ids,
    load_expectations,
    load_jd_text,
)


def test_fixture_corpus_is_paired() -> None:
    fixture_ids = list_fixture_ids()
    assert fixture_ids, "expected at least one JD fixture"
    for fixture_id in fixture_ids:
        text = load_jd_text(fixture_id)
        expect = load_expectations(fixture_id)
        assert text.strip()
        assert expect.get("id") == fixture_id
        assert "jd_parser" in expect


def test_check_jd_parser_soft_expectations() -> None:
    jd = ParsedJD(
        company="Cloudflare, Inc.",
        role="Software Engineer, Systems",
        seniority="0-2 yrs",
        domain_signals=["systems", "networking"],
    )
    expect = load_expectations("systems_cloudflare")["jd_parser"]
    checks = check_jd_parser(jd, expect)
    assert checks
    assert all(c.ok for c in checks)


def test_check_resume_selector_accepts_allowed_variant() -> None:
    selection = ResumeSelection(variant=ResumeVariant.SYSTEMS, grade="B")
    expect = load_expectations("systems_cloudflare")["resume_selector"]
    checks = check_resume_selector(selection, expect)
    assert all(c.ok for c in checks)


def test_check_cover_letter_flags_i_opener() -> None:
    letter = CoverLetter(
        body="I am excited to apply.\n\nSecond paragraph here.\n\nThird paragraph close.",
        word_count=12,
    )
    expect = {"max_words": 300, "min_paragraphs": 3, "forbid_opener_i": True}
    checks = {c.name: c.ok for c in check_cover_letter(letter, expect)}
    assert checks["forbid_opener_i"] is False
    assert checks["min_paragraphs"] is True
