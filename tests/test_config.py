from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import config
import pytest
from pydantic import ValidationError


def test_cover_letter_project_string_false_is_excluded() -> None:
    profile = {
        "projects": [
            {"name": "Excluded", "include_in_cover_letter": "false"},
            {"name": "Included", "include_in_cover_letter": "true"},
        ]
    }
    assert [project["name"] for project in config.get_cover_letter_projects(profile)] == ["Included"]


def test_ranked_projects_skips_malformed_entries() -> None:
    profile = {"projects": ["bad", None, {"name": "Valid", "tier": "A"}]}
    assert config.get_ranked_projects(profile) == [{"name": "Valid", "tier": "A"}]


def test_ranked_projects_treats_null_as_empty() -> None:
    assert config.get_ranked_projects({"projects": None}) == []


def test_user_profile_merges_explicit_grad_and_undergrad_projects(tmp_path) -> None:
    path = tmp_path / "profile.yaml"
    path.write_text(
        "projects:\n  - name: Explicit\n"
        "grad_projects:\n  - name: Graduate\n"
        "undergrad_projects:\n  - name: Undergraduate\n",
        encoding="utf-8",
    )
    config.get_user_profile.cache_clear()
    with patch("config.get_settings", return_value=SimpleNamespace(user_profile_path=str(path))):
        try:
            profile = config.get_user_profile()
        finally:
            config.get_user_profile.cache_clear()

    assert [project["name"] for project in profile["projects"]] == [
        "Explicit",
        "Graduate",
        "Undergraduate",
    ]


def test_user_profile_ignores_non_list_project_sections(tmp_path) -> None:
    path = tmp_path / "profile.yaml"
    path.write_text(
        "projects: malformed\ngrad_projects:\n  - name: Graduate\nundergrad_projects: malformed\n",
        encoding="utf-8",
    )
    config.get_user_profile.cache_clear()
    with patch("config.get_settings", return_value=SimpleNamespace(user_profile_path=str(path))):
        try:
            profile = config.get_user_profile()
        finally:
            config.get_user_profile.cache_clear()

    assert profile["projects"] == [{"name": "Graduate"}]


def test_search_settings_have_production_defaults() -> None:
    settings = config.Settings(_env_file=None)

    assert settings.search_provider == "auto"
    assert settings.search_fallback_on_empty is True
    assert settings.search_timeout_seconds == 10
    assert settings.search_max_attempts == 2
    assert settings.search_concurrency == 3


@pytest.mark.parametrize(
    ("value", "expected"),
    [("serper", "serper"), ("TAVILY", "tavily"), (" Auto ", "auto")],
)
def test_search_provider_is_normalized(value: str, expected: str) -> None:
    assert config.Settings(_env_file=None, search_provider=value).search_provider == expected


@pytest.mark.parametrize("value", ["none", "google", "", 3])
def test_search_provider_must_be_supported(value: object) -> None:
    with pytest.raises(ValidationError, match="search_provider must be one of"):
        config.Settings(_env_file=None, search_provider=value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("search_timeout_seconds", 0),
        ("search_timeout_seconds", 61),
        ("search_max_attempts", 0),
        ("search_max_attempts", 4),
        ("search_concurrency", 0),
        ("search_concurrency", 6),
    ],
)
def test_search_numeric_settings_are_bounded(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        config.Settings(_env_file=None, **{field: value})
