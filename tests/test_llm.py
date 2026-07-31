from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import BaseModel

import llm


class _Response(BaseModel):
    answer: str


def _settings() -> object:
    return type("Settings", (), {"gemini_api_key": "test-key", "gemini_model": "gemini-test"})()


@patch("llm.trace_generation")
@patch("llm._call_gemini")
@patch("llm.get_settings", side_effect=_settings)
def test_generate_structured_forwards_schema_and_validates_response(mock_settings, mock_call, mock_trace) -> None:
    mock_call.return_value = '{"answer": "ok"}'

    assert llm.generate_structured(_Response, system_prompt="system", user_prompt="user", max_tokens=128) == _Response(answer="ok")

    schema = mock_call.call_args.kwargs["schema"]
    assert schema["properties"] == {"answer": {"title": "Answer", "type": "string"}}
    assert "title" not in schema
    assert mock_call.call_args.kwargs["max_tokens"] == 128
    assert mock_trace.call_count == 1


@patch("llm.trace_generation")
@patch("llm._call_gemini", side_effect=["not json", '{"answer": "repaired"}'])
@patch("llm.get_settings", side_effect=_settings)
def test_generate_structured_repairs_once_with_larger_budget(mock_settings, mock_call, mock_trace) -> None:
    result = llm.generate_structured(_Response, system_prompt="system", user_prompt="user", max_tokens=128)

    assert result.answer == "repaired"
    assert [call.kwargs["max_tokens"] for call in mock_call.call_args_list] == [128, 256]
    assert "previous response was invalid" in mock_call.call_args_list[1].kwargs["full_prompt"]
    assert mock_trace.call_count == 2


@patch("llm._call_gemini", return_value="not json")
@patch("llm.get_settings", side_effect=_settings)
def test_generate_structured_fails_after_one_repair(mock_settings, mock_call) -> None:
    with pytest.raises(ValueError, match="valid JSON"):
        llm.generate_structured(_Response, system_prompt="system", user_prompt="user", max_tokens=128)

    assert mock_call.call_count == 2


def test_retry_token_limits_does_not_repeat_the_cap() -> None:
    assert llm._retry_token_limits(8192) == [8192]


@patch("llm.trace_generation")
@patch("llm._call_gemini_text", side_effect=["", "plain response"])
@patch("llm.get_settings", side_effect=_settings)
def test_generate_text_retries_empty_response_once(mock_settings, mock_call, mock_trace) -> None:
    result = llm.generate_text(system_prompt="system", user_prompt="user", max_tokens=128)

    assert result == "plain response"
    assert [call.kwargs["max_tokens"] for call in mock_call.call_args_list] == [128, 256]
    assert mock_trace.call_count == 1
