from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import config


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
