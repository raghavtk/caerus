from __future__ import annotations

import base64
import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from loguru import logger

from config import get_settings

_LANGFUSE_CLIENT: Any | None = None
_LANGFUSE_DISABLED = False


def _get_langfuse_client() -> Any | None:
    global _LANGFUSE_CLIENT, _LANGFUSE_DISABLED
    if _LANGFUSE_DISABLED:
        return None
    if _LANGFUSE_CLIENT is not None:
        return _LANGFUSE_CLIENT

    settings = get_settings()
    if not settings.langfuse_enabled:
        return None

    try:
        from langfuse import Langfuse
    except Exception as exc:  # pragma: no cover
        logger.warning("langfuse package not available; tracing disabled: {}", exc)
        _LANGFUSE_DISABLED = True
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
        _LANGFUSE_DISABLED = True
        return None


def make_session_id(input_text: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha256(input_text.encode()).hexdigest()[:8]
    return f"caerus-{timestamp}-{digest}"


def langfuse_basic_auth_token() -> str | None:
    settings = get_settings()
    if not settings.langfuse_enabled:
        return None
    raw = f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}"
    return base64.b64encode(raw.encode()).decode()


def build_mcp_config_json() -> str | None:
    settings = get_settings()
    if not settings.langfuse_enabled:
        return None
    token = langfuse_basic_auth_token()
    if token is None:
        return None
    import json

    payload = {
        "mcpServers": {
            "langfuse": {
                "url": settings.langfuse_mcp_url,
                "headers": {"Authorization": f"Basic {token}"},
            },
            "langfuse-docs": {"url": "https://langfuse.com/api/mcp"},
        }
    }
    return json.dumps(payload, indent=2)


def verify_langfuse() -> tuple[bool, str]:
    settings = get_settings()
    if not settings.langfuse_enabled:
        return False, "LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are not set"

    client = _get_langfuse_client()
    if client is None:
        return False, "Langfuse client failed to initialize"

    try:
        if not client.auth_check():
            return False, "Langfuse auth_check failed — verify keys and LANGFUSE_HOST"
        with client.start_as_current_observation(as_type="span", name="caerus.healthcheck") as span:
            span.update(output={"status": "ok"})
        client.flush()
        return True, f"Langfuse reachable at {settings.langfuse_host}"
    except Exception as exc:  # pragma: no cover
        return False, f"Langfuse verification failed: {exc}"


def flush_traces() -> None:
    client = _get_langfuse_client()
    if client is None:
        return
    try:
        client.flush()
    except Exception as exc:  # pragma: no cover
        logger.warning("langfuse flush failed (non-fatal): {}", exc)


class StepTrace:
    def __init__(self, pipeline: PipelineTrace, name: str) -> None:
        self._pipeline = pipeline
        self._name = name
        self._ctx: Any | None = None
        self._span: Any | None = None

    def __enter__(self) -> StepTrace:
        client = self._pipeline._client
        if client is None:
            return self
        try:
            self._ctx = client.start_as_current_observation(
                as_type="span",
                name=f"caerus.step.{self._name}",
            )
            self._span = self._ctx.__enter__()
        except Exception as exc:  # pragma: no cover
            logger.warning("langfuse step span failed (non-fatal): {}", exc)
        return self

    def set_output(self, output: dict[str, Any]) -> None:
        if self._span is None:
            return
        try:
            self._span.update(output=output)
        except Exception as exc:  # pragma: no cover
            logger.warning("langfuse step output update failed (non-fatal): {}", exc)

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._ctx is None:
            return
        try:
            if exc_val is not None and self._span is not None:
                self._span.update(level="ERROR", status_message=str(exc_val))
            self._ctx.__exit__(exc_type, exc_val, exc_tb)
        except Exception as exc:  # pragma: no cover
            logger.warning("langfuse step span close failed (non-fatal): {}", exc)


class PipelineTrace:
    def __init__(self, *, session_id: str, input_preview: str, skip_notion: bool) -> None:
        self.session_id = session_id
        self.input_preview = input_preview
        self.skip_notion = skip_notion
        self.trace_id: str | None = None
        self.trace_url: str | None = None
        self._client: Any | None = None
        self._propagate_ctx: Any | None = None
        self._root_ctx: Any | None = None
        self._root_span: Any | None = None

    def __enter__(self) -> PipelineTrace:
        self._client = _get_langfuse_client()
        if self._client is None:
            return self
        try:
            from langfuse import propagate_attributes

            self._propagate_ctx = propagate_attributes(
                session_id=self.session_id,
                tags=["caerus", "pipeline"],
            )
            self._propagate_ctx.__enter__()
            self._root_ctx = self._client.start_as_current_observation(
                as_type="span",
                name="caerus.pipeline",
                input={"input_preview": self.input_preview, "skip_notion": self.skip_notion},
                metadata={"source": "caerus-cli", "session_id": self.session_id},
            )
            self._root_span = self._root_ctx.__enter__()
            self.trace_id = self._client.get_current_trace_id()
            self.trace_url = self._client.get_trace_url(trace_id=self.trace_id)
        except Exception as exc:  # pragma: no cover
            logger.warning("langfuse pipeline trace failed (non-fatal): {}", exc)
        return self

    def step(self, name: str) -> StepTrace:
        return StepTrace(self, name)

    def set_output(self, output: dict[str, Any]) -> None:
        if self._root_span is None:
            return
        try:
            self._root_span.update(output=output)
        except Exception as exc:  # pragma: no cover
            logger.warning("langfuse pipeline output update failed (non-fatal): {}", exc)

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._client is None:
            return
        try:
            if exc_val is not None and self._root_span is not None:
                self._root_span.update(level="ERROR", status_message=str(exc_val))
            if self._root_ctx is not None:
                self._root_ctx.__exit__(exc_type, exc_val, exc_tb)
            if self._propagate_ctx is not None:
                self._propagate_ctx.__exit__(exc_type, exc_val, exc_tb)
        except Exception as exc:  # pragma: no cover
            logger.warning("langfuse pipeline trace close failed (non-fatal): {}", exc)
        finally:
            flush_traces()


def pipeline_trace(*, session_id: str, input_preview: str, skip_notion: bool = False) -> PipelineTrace:
    return PipelineTrace(session_id=session_id, input_preview=input_preview, skip_notion=skip_notion)


@contextmanager
def trace_span(name: str, input_payload: dict[str, Any] | None = None) -> Iterator[Any | None]:
    client = _get_langfuse_client()
    if client is None:
        yield None
        return
    try:
        with client.start_as_current_observation(
            as_type="span",
            name=name,
            input=input_payload or {},
        ) as span:
            yield span
    except Exception as exc:  # pragma: no cover
        logger.warning("langfuse span failed (non-fatal): {}", exc)
        yield None


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
        with client.start_as_current_observation(
            as_type="generation",
            name="caerus.llm.generate",
            model=model,
            input={"system_prompt": system_prompt, "user_prompt": user_prompt},
            metadata=metadata or {},
        ) as generation:
            generation.update(output=output_text)
    except Exception as exc:  # pragma: no cover
        logger.warning("langfuse generation trace failed (non-fatal): {}", exc)
