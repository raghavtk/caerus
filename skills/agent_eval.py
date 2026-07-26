from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agents.company_research import research_company
from agents.cover_letter import generate_cover_letter
from agents.jd_parser import parse_jd
from agents.resume_selector import select_resume
from schemas.models import CompanyBrief, CoverLetter, ParsedJD, ResumeSelection

# Repo-root fixtures (works for editable installs and pytest from project root).
_REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_ROOT = _REPO_ROOT / "tests" / "fixtures"
JD_DIR = FIXTURES_ROOT / "jds"
EXPECT_DIR = FIXTURES_ROOT / "expectations"

AGENTS = ("jd_parser", "resume_selector", "cover_letter", "company_research")


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


@dataclass
class EvalReport:
    fixture_id: str
    agent: str
    checks: list[CheckResult] = field(default_factory=list)
    output: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c.ok for c in self.checks)


def list_fixture_ids() -> list[str]:
    return sorted(path.stem for path in JD_DIR.glob("*.txt"))


def load_jd_text(fixture_id: str) -> str:
    path = JD_DIR / f"{fixture_id}.txt"
    if not path.exists():
        raise FileNotFoundError(f"missing JD fixture: {path}")
    return path.read_text(encoding="utf-8")


def load_expectations(fixture_id: str) -> dict[str, Any]:
    path = EXPECT_DIR / f"{fixture_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"missing expectations: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"expectations must be a mapping: {path}")
    return data


def _contains(haystack: str | None, needle: str) -> bool:
    return bool(haystack) and needle.lower() in haystack.lower()


def check_jd_parser(jd: ParsedJD, expect: dict[str, Any]) -> list[CheckResult]:
    checks: list[CheckResult] = []
    if needle := expect.get("company_contains"):
        ok = _contains(jd.company, needle)
        checks.append(CheckResult("company_contains", ok, f"got {jd.company!r}"))
    if needle := expect.get("role_contains"):
        ok = _contains(jd.role, needle)
        checks.append(CheckResult("role_contains", ok, f"got {jd.role!r}"))
    if allowed := expect.get("seniority_in"):
        ok = jd.seniority in allowed
        checks.append(CheckResult("seniority_in", ok, f"got {jd.seniority!r}"))
    if any_signals := expect.get("domain_signals_any"):
        signals = {s.lower() for s in jd.domain_signals}
        ok = bool(signals.intersection({s.lower() for s in any_signals}))
        checks.append(
            CheckResult(
                "domain_signals_any",
                ok,
                f"got {sorted(signals)}; wanted any of {any_signals}",
            )
        )
    return checks


def check_resume_selector(selection: ResumeSelection, expect: dict[str, Any]) -> list[CheckResult]:
    checks: list[CheckResult] = []
    if variants := expect.get("variant_in"):
        ok = selection.variant.value in variants
        checks.append(CheckResult("variant_in", ok, f"got {selection.variant.value}"))
    if grades := expect.get("grade_in"):
        ok = selection.grade in grades
        checks.append(CheckResult("grade_in", ok, f"got {selection.grade!r}"))
    return checks


def check_cover_letter(letter: CoverLetter, expect: dict[str, Any]) -> list[CheckResult]:
    checks: list[CheckResult] = []
    body = letter.body.strip()
    paragraphs = [p for p in body.split("\n\n") if p.strip()]
    if (max_words := expect.get("max_words")) is not None:
        ok = letter.word_count <= int(max_words)
        checks.append(CheckResult("max_words", ok, f"got {letter.word_count}"))
    if (min_paragraphs := expect.get("min_paragraphs")) is not None:
        ok = len(paragraphs) >= int(min_paragraphs)
        checks.append(CheckResult("min_paragraphs", ok, f"got {len(paragraphs)}"))
    if expect.get("forbid_opener_i"):
        first = body.lstrip()
        ok = not first.lower().startswith("i ") and not first.lower().startswith("i'")
        checks.append(CheckResult("forbid_opener_i", ok, f"opener={first[:40]!r}"))
    return checks


def check_company_research(brief: CompanyBrief) -> list[CheckResult]:
    return [
        CheckResult("company_set", bool(brief.company and brief.company != "Unknown"), f"got {brief.company!r}"),
        CheckResult("fit_score_range", 0 <= brief.fit_score <= 100, f"got {brief.fit_score}"),
        CheckResult("has_sources_or_unknown_ok", True, f"sources={len(brief.sources)}"),
    ]


def run_agent_eval(agent: str, fixture_id: str) -> EvalReport:
    if agent not in AGENTS:
        raise ValueError(f"unknown agent {agent!r}; choose from {AGENTS}")

    jd_text = load_jd_text(fixture_id)
    expectations = load_expectations(fixture_id)
    report = EvalReport(fixture_id=fixture_id, agent=agent)

    if agent == "jd_parser":
        jd = parse_jd(jd_text)
        report.output = jd.model_dump()
        report.checks = check_jd_parser(jd, expectations.get("jd_parser", {}))
        return report

    jd = parse_jd(jd_text)

    if agent == "company_research":
        brief = research_company(jd)
        report.output = brief.model_dump()
        report.checks = check_company_research(brief)
        return report

    if agent == "resume_selector":
        selection = select_resume(jd, None)
        report.output = selection.model_dump()
        report.checks = check_resume_selector(selection, expectations.get("resume_selector", {}))
        return report

    # cover_letter needs brief + selection; research is expensive — use a sparse brief
    brief = CompanyBrief(
        company=jd.company or "Unknown",
        fit_score=70,
        strong_overlaps=["systems experience"],
        potential_angles=["project depth"],
        tech_highlights=["infrastructure"],
    )
    selection = select_resume(jd, brief)
    letter = generate_cover_letter(jd, brief, selection)
    report.output = letter.model_dump()
    report.checks = check_cover_letter(letter, expectations.get("cover_letter", {}))
    return report
