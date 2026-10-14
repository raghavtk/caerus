from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from llm import generate_structured, generate_text


class _StructuredResponse(BaseModel):
    answer: str


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


@patch("llm.trace_generation")
@patch("llm.get_settings")
def test_generate_structured_uses_dedicated_system_instruction(mock_settings, mock_trace) -> None:
    mock_settings.return_value = SimpleNamespace(gemini_api_key="test", gemini_model="gemini-test")
    google, generate_content = _fake_google('{"answer": "ok"}')

    with patch.dict(sys.modules, {"google": google}):
        result = generate_structured(
            _StructuredResponse,
            system_prompt="trusted rules",
            user_prompt='{"untrusted": "data"}',
            max_tokens=123,
        )

    assert result.answer == "ok"
    assert generate_content.call_args.kwargs["config"]["system_instruction"] == "trusted rules"
    assert "trusted rules" not in generate_content.call_args.kwargs["contents"]


@patch("llm.trace_generation")
@patch("llm.get_settings")
def test_generate_structured_can_redact_trace_content(mock_settings, mock_trace) -> None:
    mock_settings.return_value = SimpleNamespace(gemini_api_key="test", gemini_model="gemini-test")
    google, _ = _fake_google('{"answer": "private output"}')

    with patch.dict(sys.modules, {"google": google}):
        generate_structured(
            _StructuredResponse,
            system_prompt="private system",
            user_prompt="private profile",
        )

    assert mock_trace.call_args.kwargs["system_prompt"] == "[redacted personal content]"
    assert mock_trace.call_args.kwargs["user_prompt"] == "[redacted personal content]"
    assert mock_trace.call_args.kwargs["output_text"] == "[redacted personal content]"
    assert mock_trace.call_args.kwargs["metadata"]["content_redacted"] is True


@pytest.mark.parametrize('cancel', [False, True])
async def test_async_structured_uses_native_client_and_closes_on_cancellation(cancel):
    import asyncio
    from unittest.mock import AsyncMock
    from llm import generate_structured_async

    entered = asyncio.Event()
    async def generate(**kwargs):
        entered.set()
        if cancel:
            await asyncio.Event().wait()
        return SimpleNamespace(text='{"answer": "ok"}')

    async_client = MagicMock()
    async_client.__aenter__ = AsyncMock(return_value=async_client)
    async_client.__aexit__ = AsyncMock(return_value=False)
    async_client.models.generate_content = AsyncMock(side_effect=generate)
    client = SimpleNamespace(aio=async_client)
    google = SimpleNamespace(genai=SimpleNamespace(Client=MagicMock(return_value=client)))
    with patch.dict(sys.modules, {'google': google}), patch('llm.get_settings', return_value=SimpleNamespace(
        gemini_api_key='secret', gemini_model='test'
    )), patch('llm.trace_generation') as trace:
        task = asyncio.create_task(generate_structured_async(
            _StructuredResponse, system_prompt='private system', user_prompt='private profile'
        ))
        await asyncio.wait_for(entered.wait(), 1)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            trace.assert_not_called()
        else:
            assert (await task).answer == 'ok'
            assert trace.call_args.kwargs['user_prompt'] == '[redacted personal content]'
        async_client.__aexit__.assert_awaited_once()
        async_client.models.generate_content.assert_awaited_once()
