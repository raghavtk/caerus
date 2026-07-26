from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger

from config import get_settings

_LANGFUSE_CLIENT: Any | None = None


def _get_langfuse_client() -> Any | None:
    global _LANGFUSE_CLIENT
    if _LANGFUSE_CLIENT is not None:
        return _LANGFUSE_CLIENT

    settings = get_settings()
    if not settings.langfuse_enabled:
        return None

    try:
        from langfuse import Langfuse
    except Exception as exc:  # pragma: no cover
        logger.warning("langfuse package not available; tracing disabled: {}", exc)
        return None

    try:
        _LANGFUSE_CLIENT = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        return _LANGFUSE_CLIENT
    except Exception as exc:  # pragma: no cover
        logger.warning("failed to initialize langfuse client: {}", exc)
        return None


def trace_event(name: str, payload: dict[str, Any]) -> None:
    client = _get_langfuse_client()
    if client is None:
        return
    try:
        client.event(
            name=name,
            input=payload,
            metadata={"source": "caerus-cli", "timestamp": datetime.now(timezone.utc).isoformat()},
        )
        client.flush()
    except Exception as exc:  # pragma: no cover
        logger.warning("langfuse event trace failed (non-fatal): {}", exc)


def trace_generation(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    output_text: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    client = _get_langfuse_client()
    if client is None:
        return
    try:
        client.generation(
            name="caerus.llm.generate_structured",
            model=model,
            input={"system_prompt": system_prompt, "user_prompt": user_prompt},
            output=output_text,
            metadata=metadata or {},
        )
        client.flush()
    except Exception as exc:  # pragma: no cover
        logger.warning("langfuse generation trace failed (non-fatal): {}", exc)
