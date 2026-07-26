from __future__ import annotations

from pathlib import Path

import pytest

from skills.agent_eval import LIVE_ENV_VAR, live_evals_allowed

FIXTURES_DIR = Path(__file__).parent / "fixtures"
JD_FIXTURES_DIR = FIXTURES_DIR / "jds"
EXPECTATIONS_DIR = FIXTURES_DIR / "expectations"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"live: hits Gemini / search APIs; requires {LIVE_ENV_VAR}=1",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Drop live tests from the run unless CAERUS_ALLOW_LIVE is set.

    Deselect (not skip) so default `pytest` stays quiet and does not burn quota.
    """
    if live_evals_allowed():
        return
    items[:] = [item for item in items if "live" not in item.keywords]


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def jd_fixtures_dir() -> Path:
    return JD_FIXTURES_DIR
