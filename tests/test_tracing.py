from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import pytest

from skills import tracing


@pytest.fixture(autouse=True)
def reset_tracing_state() -> None:
    tracing._LANGFUSE_CLIENT = None
    tracing._LANGFUSE_DISABLED = False
    tracing._last_init_failure_at = None
    yield
    tracing._LANGFUSE_CLIENT = None
    tracing._LANGFUSE_DISABLED = False
    tracing._last_init_failure_at = None


def test_make_session_id_format() -> None:
    session = tracing.make_session_id("input-a")
    assert session.startswith("caerus-")
    parts = session.split("-")
    assert len(parts) >= 3
    assert len(parts[-1]) == 8


def test_make_session_id_differs_by_input() -> None:
    assert tracing.make_session_id("input-a") != tracing.make_session_id("input-b")


def test_langfuse_basic_auth_token() -> None:
    with patch("skills.tracing.get_settings") as mock_settings:
        mock_settings.return_value.langfuse_enabled = True
        mock_settings.return_value.langfuse_public_key = "pk-test"
        mock_settings.return_value.langfuse_secret_key = "sk-test"
        token = tracing.langfuse_basic_auth_token()
        assert token == base64.b64encode(b"pk-test:sk-test").decode()


def test_build_mcp_config_json_includes_us_endpoint() -> None:
    with patch("skills.tracing.get_settings") as mock_settings:
        mock_settings.return_value.langfuse_enabled = True
        mock_settings.return_value.langfuse_public_key = "pk-test"
        mock_settings.return_value.langfuse_secret_key = "sk-test"
        mock_settings.return_value.langfuse_mcp_url = "https://us.cloud.langfuse.com/api/public/mcp"
        config = tracing.build_mcp_config_json()
        assert config is not None
        assert "https://us.cloud.langfuse.com/api/public/mcp" in config
        assert "langfuse-docs" in config


def test_pipeline_trace_noop_when_disabled() -> None:
    with patch("skills.tracing._get_langfuse_client", return_value=None):
        with tracing.pipeline_trace(session_id="s1", input_preview="hello") as trace:
            with trace.step("parse_jd") as step:
                step.set_output({"ok": True})
            trace.set_output({"status": "done"})
        assert trace.trace_id is None
        assert trace.trace_url is None


def test_pipeline_trace_creates_nested_spans() -> None:
    mock_client = MagicMock()
    mock_client.get_current_trace_id.return_value = "trace-123"
    mock_client.get_trace_url.return_value = "https://us.cloud.langfuse.com/trace/trace-123"

    mock_root_ctx = MagicMock()
    mock_root_span = MagicMock()
    mock_root_ctx.__enter__.return_value = mock_root_span

    mock_step_ctx = MagicMock()
    mock_step_span = MagicMock()
    mock_step_ctx.__enter__.return_value = mock_step_span

    mock_propagate_ctx = MagicMock()
    mock_client.start_as_current_observation.side_effect = [mock_root_ctx, mock_step_ctx]

    with patch("skills.tracing._get_langfuse_client", return_value=mock_client):
        with patch("langfuse.propagate_attributes", return_value=mock_propagate_ctx):
            with tracing.pipeline_trace(session_id="session-abc", input_preview="jd text") as trace:
                with trace.step("parse_jd") as step:
                    step.set_output({"company": "Acme"})
                trace.set_output({"status": "completed"})

    assert trace.trace_id == "trace-123"
    assert trace.trace_url == "https://us.cloud.langfuse.com/trace/trace-123"
    assert mock_client.start_as_current_observation.call_count == 2
    mock_root_span.update.assert_called()
    mock_step_span.update.assert_called()
    mock_client.flush.assert_called_once()


def test_trace_generation_uses_current_observation() -> None:
    mock_client = MagicMock()
    mock_ctx = MagicMock()
    mock_generation = MagicMock()
    mock_ctx.__enter__.return_value = mock_generation
    mock_client.start_as_current_observation.return_value = mock_ctx

    with patch("skills.tracing._get_langfuse_client", return_value=mock_client):
        tracing.trace_generation(
            model="gemini-2.5-flash",
            system_prompt="sys",
            user_prompt="user",
            output_text='{"ok": true}',
            metadata={"response_model": "ParsedJD"},
        )

    mock_client.start_as_current_observation.assert_called_once()
    mock_generation.update.assert_called_once_with(output='{"ok": true}')
    mock_client.flush.assert_called_once()


def test_init_failure_cooldown_avoids_repeated_init() -> None:
    with patch("skills.tracing.get_settings") as mock_settings:
        mock_settings.return_value.langfuse_enabled = True
        mock_settings.return_value.langfuse_public_key = "pk-test"
        mock_settings.return_value.langfuse_secret_key = "sk-test"
        mock_settings.return_value.langfuse_host = "https://us.cloud.langfuse.com"
        with patch("langfuse.Langfuse", side_effect=RuntimeError("bad host")) as mock_langfuse:
            assert tracing._get_langfuse_client() is None
            assert tracing._get_langfuse_client() is None
            assert mock_langfuse.call_count == 1


def test_trace_generation_flushes_when_update_fails() -> None:
    mock_client = MagicMock()
    mock_ctx = MagicMock()
    mock_generation = MagicMock()
    mock_generation.update.side_effect = RuntimeError("update failed")
    mock_ctx.__enter__.return_value = mock_generation
    mock_client.start_as_current_observation.return_value = mock_ctx

    with patch("skills.tracing._get_langfuse_client", return_value=mock_client):
        tracing.trace_generation(
            model="gemini-2.5-flash",
            system_prompt="sys",
            user_prompt="user",
            output_text='{"ok": true}',
        )

    mock_client.flush.assert_called_once()


def test_verify_langfuse_reports_missing_keys() -> None:
    with patch("skills.tracing.get_settings") as mock_settings:
        mock_settings.return_value.langfuse_enabled = False
        ok, message = tracing.verify_langfuse()
        assert ok is False
        assert "not set" in message
