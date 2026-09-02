from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import patch

import httpx
import pytest

from config import Settings, get_settings
from skills.search import (
    SearchAttempt,
    SearchOutcome,
    SearchProvider,
    SearchResult,
    _ProviderResponse,
    _search_provider,
    canonicalize_url,
    web_search,
)


def _settings(
    *,
    serper: bool = False,
    tavily: bool = False,
    provider: str = "auto",
    fallback_on_empty: bool = True,
) -> Settings:
    return Settings(
        _env_file=None,
        serper_api_key="serper-secret" if serper else None,
        tavily_api_key="tavily-secret" if tavily else None,
        search_provider=provider,
        search_fallback_on_empty=fallback_on_empty,
        search_timeout_seconds=2,
        search_max_attempts=2,
    )


def _provider_response(
    provider: SearchProvider,
    outcome: SearchOutcome,
    *,
    results: tuple[SearchResult, ...] = (),
) -> _ProviderResponse:
    return _ProviderResponse(
        results,
        SearchAttempt(
            provider=provider,
            outcome=outcome,
            http_status=None,
            retry_count=0,
            latency_ms=1,
            raw_result_count=len(results),
            accepted_result_count=len(results),
            message="fixed diagnostic",
        ),
    )


def test_invalid_request_raises_before_loading_settings() -> None:
    with patch("skills.search.get_settings") as settings:
        with pytest.raises(ValueError, match="blank"):
            web_search("   ")
        with pytest.raises(ValueError, match="between 1 and 20"):
            web_search("query", num_results=0)
        with pytest.raises(ValueError, match="between 1 and 20"):
            web_search("query", num_results=21)
    settings.assert_not_called()


def test_serper_parsing_canonicalization_metadata_and_limit() -> None:
    current_utc = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-API-KEY"] == "secret"
        return httpx.Response(
            200,
            json={
                "organic": [
                    {
                        "title": "One",
                        "link": "HTTPS://Example.com/a/?utm_source=x#fragment",
                        "snippet": "First",
                        "position": 3,
                        "date": "2 days ago",
                    },
                    {"title": "Duplicate", "link": "https://example.com/a", "snippet": "x"},
                    {"title": "Two", "link": "https://other.test/b", "snippet": "Second"},
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        response = _search_provider(
            "serper",
            query="acme",
            num_results=2,
            api_key="secret",
            timeout_seconds=2,
            max_attempts=2,
            client=client,
            sleep=lambda _: None,
            now_utc=lambda: current_utc,
        )

    assert [result.url for result in response.results] == [
        "https://example.com/a",
        "https://other.test/b",
    ]
    assert response.results[0].rank == 3
    assert response.results[0].published_date == "2026-08-31T12:00:00Z"
    assert response.results[0].provider == "serper"
    assert response.results[0].query == "acme"
    assert response.attempt.raw_result_count == 3
    assert response.attempt.accepted_result_count == 2


def test_tavily_uses_bearer_auth_and_preserves_score_and_date() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret"
        assert b"secret" not in request.content
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "News",
                        "url": "https://news.test/story",
                        "content": "Details",
                        "score": 0.91,
                        "published_date": "2026-08-31",
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        response = _search_provider(
            "tavily",
            query="acme news",
            num_results=4,
            api_key="secret",
            timeout_seconds=2,
            max_attempts=2,
            client=client,
            sleep=lambda _: None,
        )

    assert response.results[0].score == pytest.approx(0.91)
    assert response.results[0].rank == 1
    assert response.results[0].published_date == "2026-08-31"


@pytest.mark.parametrize("status", [408, 429, 500, 503])
def test_retryable_statuses_are_bounded(status: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, headers={"Retry-After": "0"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        response = _search_provider(
            "serper",
            query="query",
            num_results=2,
            api_key="secret",
            timeout_seconds=2,
            max_attempts=2,
            client=client,
            sleep=lambda _: None,
        )

    assert calls == 2
    assert response.attempt.retry_count == 1
    assert response.attempt.outcome in {
        SearchOutcome.TIMEOUT,
        SearchOutcome.RATE_LIMITED,
        SearchOutcome.SERVER_ERROR,
    }


@pytest.mark.parametrize("status", [400, 401, 403, 432, 433])
def test_permanent_statuses_do_not_retry(status: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        response = _search_provider(
            "tavily",
            query="query",
            num_results=2,
            api_key="secret",
            timeout_seconds=2,
            max_attempts=3,
            client=client,
            sleep=lambda _: None,
        )

    assert calls == 1
    assert response.attempt.retry_count == 0


def test_timeout_retries_but_malformed_json_does_not() -> None:
    timeout_calls = 0

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        nonlocal timeout_calls
        timeout_calls += 1
        raise httpx.ReadTimeout("secret should never be surfaced", request=request)

    with httpx.Client(transport=httpx.MockTransport(timeout_handler)) as client:
        timeout_response = _search_provider(
            "serper",
            query="query",
            num_results=2,
            api_key="secret",
            timeout_seconds=2,
            max_attempts=2,
            client=client,
            sleep=lambda _: None,
        )

    malformed_calls = 0

    def malformed_handler(request: httpx.Request) -> httpx.Response:
        nonlocal malformed_calls
        malformed_calls += 1
        return httpx.Response(200, content=b"not-json")

    with httpx.Client(transport=httpx.MockTransport(malformed_handler)) as client:
        malformed_response = _search_provider(
            "serper",
            query="query",
            num_results=2,
            api_key="secret",
            timeout_seconds=2,
            max_attempts=2,
            client=client,
            sleep=lambda _: None,
        )

    assert timeout_calls == 2
    assert timeout_response.attempt.outcome == SearchOutcome.TIMEOUT
    assert "secret" not in timeout_response.attempt.message
    assert malformed_calls == 1
    assert malformed_response.attempt.outcome == SearchOutcome.MALFORMED_RESPONSE


class _AdvancingClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class _TrackingStream(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes], clock: _AdvancingClock | None = None) -> None:
        self.chunks = chunks
        self.clock = clock
        self.closed = False

    def __iter__(self):
        for chunk in self.chunks:
            if self.clock is not None:
                self.clock.advance(0.6)
            yield chunk

    def close(self) -> None:
        self.closed = True


def test_streamed_response_stops_slow_drip_at_absolute_deadline() -> None:
    clock = _AdvancingClock()
    stream = _TrackingStream(
        [b'{"organic":[', b'{"title":"late"}', b"]}"],
        clock,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        response = _search_provider(
            "serper",
            query="query",
            num_results=2,
            api_key="secret",
            timeout_seconds=1,
            max_attempts=1,
            client=client,
            monotonic=clock,
        )

    assert response.attempt.outcome == SearchOutcome.TIMEOUT
    assert response.attempt.retry_count == 0
    assert stream.closed is True


def test_streamed_response_aborts_and_closes_when_body_is_oversized() -> None:
    stream = _TrackingStream([b"x" * 200_000, b"y" * 100_000])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        response = _search_provider(
            "serper",
            query="query",
            num_results=2,
            api_key="secret",
            timeout_seconds=2,
            max_attempts=2,
            client=client,
        )

    assert response.attempt.outcome == SearchOutcome.MALFORMED_RESPONSE
    assert response.attempt.retry_count == 0
    assert response.attempt.accepted_result_count == 0
    assert stream.closed is True


@pytest.mark.parametrize("header_kind", ["numeric", "http-date"])
def test_retry_after_beyond_deadline_does_not_retry(header_kind: str) -> None:
    current_utc = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
    retry_after = (
        "10"
        if header_kind == "numeric"
        else format_datetime(current_utc + timedelta(seconds=10), usegmt=True)
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": retry_after})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        response = _search_provider(
            "serper",
            query="query",
            num_results=2,
            api_key="secret",
            timeout_seconds=2,
            max_attempts=2,
            client=client,
            sleep=lambda _: None,
            now_utc=lambda: current_utc,
        )

    assert calls == 1
    assert response.attempt.outcome == SearchOutcome.RATE_LIMITED
    assert response.attempt.retry_count == 0
    assert "exceeds provider deadline" in response.attempt.message


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("3 hours ago", "2026-09-02T09:00:00Z"),
        ("2 days ago", "2026-08-31T12:00:00Z"),
        ("a week ago", "2026-08-26T12:00:00Z"),
        ("yesterday", "2026-09-01T12:00:00Z"),
    ],
)
def test_serper_relative_dates_are_normalized_with_injected_clock(
    relative: str, expected: str
) -> None:
    current_utc = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "organic": [
                    {
                        "title": "News",
                        "link": "https://news.test/story",
                        "date": relative,
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        response = _search_provider(
            "serper",
            query="query",
            num_results=1,
            api_key="secret",
            timeout_seconds=2,
            max_attempts=1,
            client=client,
            now_utc=lambda: current_utc,
        )

    assert response.results[0].published_date == expected


@patch("skills.search._search_provider")
@patch("skills.search.get_settings")
def test_auto_falls_back_after_serper_failure(mock_settings, search_provider) -> None:
    mock_settings.return_value = _settings(serper=True, tavily=True)
    result = SearchResult("A", "B", "https://a.test", "tavily", "query")
    search_provider.side_effect = [
        _provider_response("serper", SearchOutcome.TIMEOUT),
        _provider_response("tavily", SearchOutcome.SUCCESS, results=(result,)),
    ]

    response = web_search(" query ")

    assert response.results == (result,)
    assert response.selected_provider == "tavily"
    assert response.fallback_reason == "serper_timeout"
    assert [attempt.provider for attempt in response.attempts] == ["serper", "tavily"]


@patch("skills.search._search_provider")
@patch("skills.search.get_settings")
def test_empty_fallback_can_be_disabled(mock_settings, search_provider) -> None:
    mock_settings.return_value = _settings(
        serper=True, tavily=True, fallback_on_empty=False
    )
    search_provider.return_value = _provider_response("serper", SearchOutcome.EMPTY)

    response = web_search("query")

    assert response.results == ()
    assert response.selected_provider == "serper"
    assert search_provider.call_count == 1


@patch("skills.search._search_provider")
@patch("skills.search.get_settings")
def test_forced_provider_never_calls_fallback(mock_settings, search_provider) -> None:
    mock_settings.return_value = _settings(
        serper=True, tavily=True, provider="tavily"
    )
    search_provider.return_value = _provider_response("tavily", SearchOutcome.EMPTY)

    response = web_search("query")

    assert [attempt.provider for attempt in response.attempts] == ["tavily"]
    assert search_provider.call_args.args[0] == "tavily"


@patch("skills.search.get_settings")
def test_no_provider_is_distinct_from_valid_empty_results(mock_settings) -> None:
    mock_settings.return_value = _settings()

    response = web_search("query")

    assert response.results == ()
    assert response.attempts == ()
    assert response.fallback_reason == "no_provider_configured"


def test_canonicalize_url_rejects_unsafe_and_deduplicates_tracking_variants() -> None:
    assert canonicalize_url("javascript:alert(1)") is None
    assert canonicalize_url("https://user:pass@example.com/a") is None
    assert canonicalize_url("HTTPS://Example.com:443/a/?b=2&utm_source=x&a=1#part") == (
        "https://example.com/a?a=1&b=2"
    )


@pytest.mark.live
@pytest.mark.parametrize("provider", ["serper", "tavily"])
def test_live_provider_smoke(provider: str) -> None:
    settings = get_settings()
    api_key = settings.serper_api_key if provider == "serper" else settings.tavily_api_key
    if not api_key:
        pytest.skip(f"{provider} API key is not configured")
    forced = settings.model_copy(update={"search_provider": provider})
    with patch("skills.search.get_settings", return_value=forced):
        response = web_search("OpenAI official website", num_results=1)

    assert response.attempts
    assert response.attempts[0].provider == provider
