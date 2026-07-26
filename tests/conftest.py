from __future__ import annotations

import os
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
JD_FIXTURES_DIR = FIXTURES_DIR / "jds"
EXPECTATIONS_DIR = FIXTURES_DIR / "expectations"

LIVE_ENV_VAR = "CAERUS_ALLOW_LIVE"


def _live_allowed() -> bool:
    return os.getenv(LIVE_ENV_VAR, "").strip() in {"1", "true", "yes"}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "live: hits Gemini / search APIs; requires CAERUS_ALLOW_LIVE=1",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Drop live tests from the run unless CAERUS_ALLOW_LIVE is set.

    Deselect (not skip) so default `pytest` stays quiet and does not burn quota.
    """
    if _live_allowed():
        return
    items[:] = [item for item in items if "live" not in item.keywords]


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def jd_fixtures_dir() -> Path:
    return JD_FIXTURES_DIR
