from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from google.genai.client import AsyncClient
from pydantic import BaseModel

from llm import generate_structured_async


async def test_real_sdk_async_context_manager_generates_and_closes():
    """Exercise the installed SDK lifecycle, which mocked clients cannot verify."""
    class Answer(BaseModel):
        answer: str

    backend = SimpleNamespace(aclose=AsyncMock())
    async_client = AsyncClient(backend)
    settings = SimpleNamespace(gemini_api_key="test-key", gemini_model="test-model")
    with (
        patch("google.genai.Client", return_value=SimpleNamespace(aio=async_client)),
        patch.object(
            async_client.models,
            "generate_content",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(text='{"answer": "ok"}'),
        ) as generate,
        patch("llm.get_settings", return_value=settings),
        patch("llm.trace_generation"),
    ):
        result = await generate_structured_async(
            Answer, system_prompt="Return an answer", user_prompt="Example"
        )
        assert result.answer == "ok"
        generate.assert_awaited_once()
        backend.aclose.assert_awaited_once()
