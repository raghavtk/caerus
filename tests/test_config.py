from __future__ import annotations

from types import SimpleNamespace

import config


def test_get_user_profile_uses_example_when_local_profile_is_absent(tmp_path, monkeypatch) -> None:
    context_dir = tmp_path / "context"
    context_dir.mkdir()
    (context_dir / "user_profile.example.yaml").write_text("name: Example Candidate\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(user_profile_path="context/user_profile.yaml"))
    config.get_user_profile.cache_clear()

    try:
        assert config.get_user_profile() == {"name": "Example Candidate"}
    finally:
        config.get_user_profile.cache_clear()
