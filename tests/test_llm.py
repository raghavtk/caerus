from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from llm import generate_text


def _fake_google(response_text: str) -> tuple[object, MagicMock]:
    generate_content = MagicMock(return_value=SimpleNamespace(text=response_text))
    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    google = SimpleNamespace(genai=SimpleNamespace(Client=MagicMock(return_value=client)))
    return google, generate_content


@patch("llm.trace_generation")
@patch("llm.get_settings")
def test_generate_text_returns_plain_text_and_redacts_trace(mock_settings, mock_trace) -> None:
    mock_settings.return_value = SimpleNamespace(gemini_api_key="test", gemini_model="gemini-test")
    google, generate_content = _fake_google("  plain response  ")

    with patch.dict(sys.modules, {"google": google}):
        result = generate_text(
            system_prompt="private system",
            user_prompt="private profile",
            max_tokens=321,
            trace_content=False,
        )

    assert result == "plain response"
    assert generate_content.call_count == 1
    assert generate_content.call_args.kwargs["config"] == {
        "system_instruction": "private system",
        "max_output_tokens": 321,
    }
    assert "private system" not in generate_content.call_args.kwargs["contents"]
    assert "private profile" in generate_content.call_args.kwargs["contents"]
    assert mock_trace.call_args.kwargs["system_prompt"] == "[redacted personal content]"
    assert mock_trace.call_args.kwargs["user_prompt"] == "[redacted personal content]"
    assert mock_trace.call_args.kwargs["output_text"] == "[redacted personal content]"
    assert mock_trace.call_args.kwargs["metadata"]["content_redacted"] is True


@patch("llm.get_settings")
def test_generate_text_rejects_empty_response_without_retry(mock_settings) -> None:
    mock_settings.return_value = SimpleNamespace(gemini_api_key="test", gemini_model="gemini-test")
    google, generate_content = _fake_google("  ")

    with patch.dict(sys.modules, {"google": google}):
        with pytest.raises(ValueError, match="empty response"):
            generate_text(system_prompt="system", user_prompt="user")

    assert generate_content.call_count == 1
