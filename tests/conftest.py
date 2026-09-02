from __future__ import annotations

from pathlib import Path

import pytest

import config as app_config
from skills.agent_eval import LIVE_ENV_VAR, live_evals_allowed
from skills import tracing

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


@pytest.fixture(autouse=True)
def disable_external_tracing_for_offline_tests(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
):
    """Prevent local .env credentials from exporting traces during offline tests."""
    if "live" in request.node.keywords and live_evals_allowed():
        yield
        return

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "")
    app_config.get_settings.cache_clear()
    tracing._LANGFUSE_CLIENT = None
    try:
        yield
    finally:
        tracing._LANGFUSE_CLIENT = None
        app_config.get_settings.cache_clear()


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def jd_fixtures_dir() -> Path:
    return JD_FIXTURES_DIR
