from __future__ import annotations

import json
from typing import Any, TypeVar

from loguru import logger
from pydantic import BaseModel, ValidationError

from config import get_settings
from skills.tracing import trace_generation

T = TypeVar("T", bound=BaseModel)


def _schema_for_gemini(response_model: type[BaseModel]) -> dict[str, Any]:
    schema = response_model.model_json_schema()
    schema.pop("title", None)
    return schema


def _retry_token_limits(max_tokens: int) -> list[int]:
    retry_limit = min(max_tokens * 2, 8192)
    return [max_tokens] if retry_limit == max_tokens else [max_tokens, retry_limit]


def _call_gemini_text(
    *,
    model: str,
    api_key: str,
    full_prompt: str,
    max_tokens: int,
) -> str:
    from google import genai

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=full_prompt,
        config={"max_output_tokens": max_tokens},
    )
    return (response.text or "").strip()


def _call_gemini(
    *,
    model: str,
    api_key: str,
    full_prompt: str,
    schema: dict[str, Any],
    max_tokens: int,
) -> str:
    from google import genai

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=full_prompt,
        config={
            "response_mime_type": "application/json",
            "response_json_schema": schema,
            "max_output_tokens": max_tokens,
        },
    )
    return (response.text or "").strip()


def generate_structured(
    response_model: type[T],
    *,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 2048,
) -> T:
    settings = get_settings()
    if not settings.gemini_api_key:
        raise ValueError("GEMINI_API_KEY is not configured")

    schema = _schema_for_gemini(response_model)
    schema_hint = json.dumps(schema, indent=2)
    full_prompt = (
        f"{system_prompt.strip()}\n\n"
        f"Return JSON matching this schema exactly (field names and types):\n{schema_hint}\n\n"
        f"USER INPUT:\n{user_prompt}\n\n"
        "Return JSON only. Do not wrap in markdown."
    )

    token_limits = _retry_token_limits(max_tokens)
    last_error: Exception | None = None

    for attempt, token_limit in enumerate(token_limits, start=1):
        retry_instruction = ""
        if attempt > 1:
            retry_instruction = (
                "\n\nYour previous response was invalid. Repair it and return only JSON that "
                "matches the requested schema exactly."
            )
        text = _call_gemini(
            model=settings.gemini_model,
            api_key=settings.gemini_api_key,
            full_prompt=full_prompt + retry_instruction,
            schema=schema,
            max_tokens=token_limit,
        )
        if not text:
            last_error = ValueError("Gemini returned empty response")
            continue

        trace_generation(
            model=settings.gemini_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            output_text=text,
            metadata={
                "max_tokens": token_limit,
                "response_model": response_model.__name__,
                "attempt": attempt,
            },
        )

        try:
            data = json.loads(text)
            return response_model.model_validate(data)
        except json.JSONDecodeError as exc:
            logger.debug("invalid JSON from Gemini (attempt {}): {}", attempt, text[:500])
            last_error = ValueError("Gemini did not return valid JSON")
            last_error.__cause__ = exc
        except ValidationError as exc:
            logger.debug("invalid schema from Gemini (attempt {}): {}", attempt, text[:500])
            last_error = ValueError("Gemini returned JSON that failed validation")
            last_error.__cause__ = exc

        if attempt < len(token_limits):
            logger.warning(
                "structured generation failed for {}; retrying with {} max tokens",
                response_model.__name__,
                token_limits[attempt],
            )

    assert last_error is not None
    raise last_error


def generate_text(
    *,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 4096,
) -> str:
    settings = get_settings()
    if not settings.gemini_api_key:
        raise ValueError("GEMINI_API_KEY is not configured")

    full_prompt = f"{system_prompt.strip()}\n\nUSER INPUT:\n{user_prompt}\n\nReturn plain text only."
    token_limits = _retry_token_limits(max_tokens)
    last_error: Exception | None = None

    for attempt, token_limit in enumerate(token_limits, start=1):
        text = _call_gemini_text(
            model=settings.gemini_model,
            api_key=settings.gemini_api_key,
            full_prompt=full_prompt,
            max_tokens=token_limit,
        )
        if text:
            trace_generation(
                model=settings.gemini_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                output_text=text,
                metadata={"max_tokens": token_limit, "response_model": "text", "attempt": attempt},
            )
            return text

        last_error = ValueError("Gemini returned empty response")
        if attempt < len(token_limits):
            logger.warning("text generation returned empty; retrying with {} max tokens", token_limits[attempt])

    assert last_error is not None
    raise last_error
