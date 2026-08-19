from __future__ import annotations

import json
from typing import TypeVar

from loguru import logger
from pydantic import BaseModel

from config import get_settings
from skills.tracing import trace_generation

T = TypeVar("T", bound=BaseModel)


def generate_structured(
    response_model: type[T],
    *,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 2048,
) -> T:
    from google import genai

    settings = get_settings()
    if not settings.gemini_api_key:
        raise ValueError("GEMINI_API_KEY is not configured")

    client = genai.Client(api_key=settings.gemini_api_key)
    full_prompt = f"{system_prompt}\n\nUSER INPUT:\n{user_prompt}\n\nReturn JSON only."
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=full_prompt,
        config={
            "response_mime_type": "application/json",
            "max_output_tokens": max_tokens,
        },
    )

    text = (response.text or "").strip()
    if not text:
        raise ValueError("Gemini returned empty response")

    trace_generation(
        model=settings.gemini_model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        output_text=text,
        metadata={"max_tokens": max_tokens, "response_model": response_model.__name__},
    )

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.debug("invalid JSON from Gemini: {}", text[:300])
        raise ValueError("Gemini did not return valid JSON") from exc

    return response_model.model_validate(data)
