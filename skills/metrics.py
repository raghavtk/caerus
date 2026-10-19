"""Local, task-isolated pipeline accounting; never stores prompts or credentials."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter

from pydantic import BaseModel, Field


class StageMetrics(BaseModel):
    latency_seconds: float = 0
    requests: dict[str, int] = Field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    usage_missing_requests: int = 0
    estimated_cost_usd: float | None = 0
    status: str = "running"


_active: ContextVar[StageMetrics | None] = ContextVar("caerus_metrics", default=None)


class RunMetrics(BaseModel):
    stages: dict[str, StageMetrics] = Field(default_factory=dict)
    cost_scope: str = "LLM token cost only; excludes search, Notion and infrastructure"

    @contextmanager
    def stage(self, name: str):
        metric = StageMetrics()
        self.stages[name] = metric
        token = _active.set(metric)
        start = perf_counter()
        try:
            yield metric
            metric.status = "completed"
        except BaseException:
            metric.status = "failed"
            raise
        finally:
            metric.latency_seconds = perf_counter() - start
            if metric.usage_missing_requests:
                metric.estimated_cost_usd = None
            _active.reset(token)


def record_request(provider: str) -> None:
    metric = _active.get()
    if metric is not None:
        metric.requests[provider] = metric.requests.get(provider, 0) + 1
        if provider == "gemini":
            metric.usage_missing_requests += 1


def record_usage(response, *, input_rate: float | None, output_rate: float | None) -> None:
    metric = _active.get()
    if metric is None:
        return
    usage = getattr(response, "usage_metadata", None)
    incoming = getattr(usage, "prompt_token_count", None)
    outgoing = getattr(usage, "candidates_token_count", None)
    thinking = getattr(usage, "thoughts_token_count", None) or 0
    if not isinstance(incoming, int) or not isinstance(outgoing, int):
        metric.usage_missing_requests = max(1, metric.usage_missing_requests)
        metric.estimated_cost_usd = None
        return
    metric.input_tokens += incoming
    metric.output_tokens += outgoing + thinking
    metric.usage_missing_requests = max(0, metric.usage_missing_requests - 1)
    if input_rate is None or output_rate is None:
        metric.estimated_cost_usd = None
    elif metric.estimated_cost_usd is not None:
        metric.estimated_cost_usd += (incoming * input_rate + (outgoing + thinking) * output_rate) / 1_000_000
