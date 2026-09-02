from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
import re
from typing import Callable, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from loguru import logger

from config import Settings, get_settings
from skills.tracing import trace_span

SearchProvider = Literal["serper", "tavily"]
_TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}
_MAX_RESPONSE_BYTES = 256 * 1024
_RELATIVE_DATE = re.compile(
    r"^(?:about\s+)?(?P<count>\d+|a|an|one)\s+"
    r"(?P<unit>min(?:ute)?|hr|hour|day|week|month|year)s?\s+ago$",
    re.IGNORECASE,
)


class _ResponseTooLarge(ValueError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SearchOutcome(str, Enum):
    SUCCESS = "success"
    EMPTY = "empty"
    TIMEOUT = "timeout"
    TRANSPORT_ERROR = "transport_error"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    AUTH_ERROR = "auth_error"
    REQUEST_ERROR = "request_error"
    QUOTA_EXHAUSTED = "quota_exhausted"
    MALFORMED_RESPONSE = "malformed_response"
    NOT_CONFIGURED = "not_configured"


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    snippet: str
    url: str
    provider: SearchProvider
    query: str
    published_date: str | None = None
    rank: int | None = None
    score: float | None = None


@dataclass(frozen=True, slots=True)
class SearchAttempt:
    provider: SearchProvider
    outcome: SearchOutcome
    http_status: int | None
    retry_count: int
    latency_ms: int
    raw_result_count: int
    accepted_result_count: int
    message: str


@dataclass(frozen=True, slots=True)
class SearchResponse:
    results: tuple[SearchResult, ...]
    attempts: tuple[SearchAttempt, ...]
    selected_provider: SearchProvider | None
    fallback_reason: str | None = None


@dataclass(frozen=True, slots=True)
class _ProviderResponse:
    results: tuple[SearchResult, ...]
    attempt: SearchAttempt


@dataclass(frozen=True, slots=True)
class _ClassifiedStatus:
    outcome: SearchOutcome
    message: str
    retryable: bool


def canonicalize_url(value: object) -> str | None:
    """Return a stable, safe HTTP(S) URL suitable for deduplication."""
    try:
        parsed = urlsplit(str(value).strip())
        if parsed.username or parsed.password:
            return None
        scheme = parsed.scheme.casefold()
        hostname = parsed.hostname.casefold() if parsed.hostname else ""
        if scheme not in {"http", "https"} or not hostname:
            return None
        default_port = (scheme, parsed.port) in {("http", 80), ("https", 443)}
        port = f":{parsed.port}" if parsed.port and not default_port else ""
    except (TypeError, ValueError):
        return None

    path = parsed.path.rstrip("/")
    query = sorted(
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in _TRACKING_PARAMS
    )
    return urlunsplit((scheme, hostname + port, path, urlencode(query), ""))


def web_search(query: str, num_results: int = 5) -> SearchResponse:
    """Search configured providers with bounded retries and typed diagnostics."""
    query = query.strip()
    if not query:
        raise ValueError("query must not be blank")
    if not 1 <= num_results <= 20:
        raise ValueError("num_results must be between 1 and 20")

    settings = get_settings()
    providers = _provider_order(settings)
    attempts: list[SearchAttempt] = []
    selected_provider: SearchProvider | None = None
    fallback_reason: str | None = None

    with trace_span(
        "caerus.tool.web_search",
        {"query": query, "providers": providers, "result_limit": num_results},
    ) as span:
        if not providers:
            logger.warning("no search provider configured; returning diagnostics without results")
            response = SearchResponse((), (), None, "no_provider_configured")
            _trace_response(span, response)
            return response

        for index, provider in enumerate(providers):
            api_key = _provider_key(settings, provider)
            if not api_key:
                attempt = SearchAttempt(
                    provider=provider,
                    outcome=SearchOutcome.NOT_CONFIGURED,
                    http_status=None,
                    retry_count=0,
                    latency_ms=0,
                    raw_result_count=0,
                    accepted_result_count=0,
                    message="API key is not configured",
                )
                attempts.append(attempt)
                provider_response = _ProviderResponse((), attempt)
            else:
                provider_response = _search_provider(
                    provider,
                    query=query,
                    num_results=num_results,
                    api_key=api_key,
                    timeout_seconds=settings.search_timeout_seconds,
                    max_attempts=settings.search_max_attempts,
                )
                attempts.append(provider_response.attempt)

            if provider_response.attempt.outcome in {SearchOutcome.SUCCESS, SearchOutcome.EMPTY}:
                selected_provider = provider
            if provider_response.results:
                response = SearchResponse(
                    provider_response.results,
                    tuple(attempts),
                    provider,
                    fallback_reason,
                )
                _trace_response(span, response)
                return response

            is_empty = provider_response.attempt.outcome == SearchOutcome.EMPTY
            has_next = index + 1 < len(providers)
            if is_empty and (not settings.search_fallback_on_empty or not has_next):
                break
            if has_next:
                fallback_reason = f"{provider}_{provider_response.attempt.outcome.value}"
                logger.warning(
                    "{} search produced {}; trying configured fallback",
                    provider,
                    provider_response.attempt.outcome.value,
                )

        response = SearchResponse((), tuple(attempts), selected_provider, fallback_reason)
        _trace_response(span, response)
        return response


def _provider_order(settings: Settings) -> list[SearchProvider]:
    if settings.search_provider == "serper":
        return ["serper"]
    if settings.search_provider == "tavily":
        return ["tavily"]
    providers: list[SearchProvider] = []
    if settings.serper_api_key:
        providers.append("serper")
    if settings.tavily_api_key:
        providers.append("tavily")
    return providers


def _provider_key(settings: Settings, provider: SearchProvider) -> str | None:
    return settings.serper_api_key if provider == "serper" else settings.tavily_api_key


def _search_provider(
    provider: SearchProvider,
    *,
    query: str,
    num_results: int,
    api_key: str,
    timeout_seconds: int,
    max_attempts: int,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    now_utc: Callable[[], datetime] = _utc_now,
) -> _ProviderResponse:
    started = monotonic()
    deadline = started + timeout_seconds
    attempts_made = 0
    last_outcome = SearchOutcome.TRANSPORT_ERROR
    last_status: int | None = None
    last_message = "provider request failed"
    raw_count = 0
    accepted: tuple[SearchResult, ...] = ()
    owns_client = client is None
    search_client = client or httpx.Client(follow_redirects=False)

    try:
        for attempt_number in range(1, max_attempts + 1):
            remaining = deadline - monotonic()
            if remaining <= 0:
                last_outcome = SearchOutcome.TIMEOUT
                last_message = "provider deadline exhausted"
                break
            attempts_made = attempt_number
            try:
                response = _send_request(
                    search_client,
                    provider,
                    query=query,
                    num_results=num_results,
                    api_key=api_key,
                    deadline=deadline,
                    monotonic=monotonic,
                )
            except _ResponseTooLarge:
                last_outcome = SearchOutcome.MALFORMED_RESPONSE
                last_status = None
                last_message = "provider response exceeded the size limit"
                retryable = False
                response = None
            except httpx.TimeoutException:
                last_outcome = SearchOutcome.TIMEOUT
                last_status = None
                last_message = "provider request timed out"
                retryable = True
                response = None
            except (httpx.NetworkError, httpx.RemoteProtocolError, httpx.ProxyError):
                last_outcome = SearchOutcome.TRANSPORT_ERROR
                last_status = None
                last_message = "transient provider transport failure"
                retryable = True
                response = None
            except httpx.HTTPError:
                last_outcome = SearchOutcome.REQUEST_ERROR
                last_status = None
                last_message = "non-retryable provider request failure"
                retryable = False
                response = None
            except Exception:
                last_outcome = SearchOutcome.REQUEST_ERROR
                last_status = None
                last_message = "unexpected provider request failure"
                retryable = False
                response = None
            else:
                last_status = response.status_code
                if 200 <= response.status_code < 300:
                    try:
                        accepted, raw_count = _parse_response(
                            provider,
                            response,
                            query=query,
                            limit=num_results,
                            current_utc=now_utc(),
                        )
                    except (TypeError, ValueError):
                        last_outcome = SearchOutcome.MALFORMED_RESPONSE
                        last_message = "provider returned a malformed response"
                        retryable = False
                    else:
                        last_outcome = SearchOutcome.SUCCESS if accepted else SearchOutcome.EMPTY
                        last_message = (
                            "provider returned usable results"
                            if accepted
                            else "provider returned no usable results"
                        )
                        break
                else:
                    classified = _classify_status(provider, response.status_code)
                    last_outcome = classified.outcome
                    last_message = classified.message
                    retryable = classified.retryable

            if not retryable or attempt_number >= max_attempts:
                break
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            delay = _retry_delay(
                response,
                attempt_number,
                remaining,
                current_utc=now_utc(),
            )
            if delay is None:
                last_message = f"{last_message}; retry delay exceeds provider deadline"
                break
            if delay > 0:
                sleep(delay)
    finally:
        if owns_client:
            search_client.close()

    elapsed_ms = max(0, round((monotonic() - started) * 1000))
    attempt = SearchAttempt(
        provider=provider,
        outcome=last_outcome,
        http_status=last_status,
        retry_count=max(0, attempts_made - 1),
        latency_ms=elapsed_ms,
        raw_result_count=raw_count,
        accepted_result_count=len(accepted),
        message=last_message,
    )
    return _ProviderResponse(accepted, attempt)


def _send_request(
    client: httpx.Client,
    provider: SearchProvider,
    *,
    query: str,
    num_results: int,
    api_key: str,
    deadline: float,
    monotonic: Callable[[], float],
) -> httpx.Response:
    timeout_seconds = deadline - monotonic()
    if timeout_seconds <= 0:
        raise httpx.ReadTimeout("provider deadline exhausted")
    timeout = httpx.Timeout(
        timeout_seconds,
        connect=min(timeout_seconds, 5.0),
        read=timeout_seconds,
        write=timeout_seconds,
        pool=min(timeout_seconds, 5.0),
    )
    if provider == "serper":
        url = "https://google.serper.dev/search"
        payload = {"q": query, "num": num_results}
        headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    else:
        url = "https://api.tavily.com/search"
        payload = {"query": query, "max_results": num_results}
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    with client.stream("POST", url, json=payload, headers=headers, timeout=timeout) as streamed:
        content_length = streamed.headers.get("Content-Length")
        try:
            declared_size = int(content_length) if content_length is not None else None
        except ValueError:
            declared_size = None
        if declared_size is not None and declared_size > _MAX_RESPONSE_BYTES:
            raise _ResponseTooLarge

        chunks: list[bytes] = []
        total_bytes = 0
        for chunk in streamed.iter_bytes():
            if monotonic() >= deadline:
                raise httpx.ReadTimeout("provider deadline exhausted", request=streamed.request)
            total_bytes += len(chunk)
            if total_bytes > _MAX_RESPONSE_BYTES:
                raise _ResponseTooLarge
            chunks.append(chunk)
        if monotonic() > deadline:
            raise httpx.ReadTimeout("provider deadline exhausted", request=streamed.request)
        return httpx.Response(
            streamed.status_code,
            headers=streamed.headers,
            content=b"".join(chunks),
            request=streamed.request,
        )


def _parse_response(
    provider: SearchProvider,
    response: httpx.Response,
    *,
    query: str,
    limit: int,
    current_utc: datetime,
) -> tuple[tuple[SearchResult, ...], int]:
    try:
        data = response.json()
    except Exception as exc:
        raise ValueError("invalid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("response root is not an object")
    key = "organic" if provider == "serper" else "results"
    items = data.get(key, [])
    if not isinstance(items, list):
        raise ValueError("results field is not a list")
    return (
        _normalize_results(
            items,
            provider=provider,
            query=query,
            limit=limit,
            current_utc=current_utc,
        ),
        len(items),
    )


def _normalize_results(
    items: object,
    *,
    provider: SearchProvider,
    query: str,
    limit: int,
    current_utc: datetime,
) -> tuple[SearchResult, ...]:
    if not isinstance(items, list):
        raise ValueError("results field is not a list")

    accepted: list[SearchResult] = []
    seen_urls: set[str] = set()
    url_key = "link" if provider == "serper" else "url"
    snippet_key = "snippet" if provider == "serper" else "content"
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        url = canonicalize_url(item.get(url_key))
        title = str(item.get("title") or "").strip()
        snippet = str(item.get(snippet_key) or "").strip()
        if not url or url in seen_urls or (not title and not snippet):
            continue
        seen_urls.add(url)
        rank = _optional_int(item.get("position")) if provider == "serper" else index
        score = _optional_float(item.get("score")) if provider == "tavily" else None
        published_date = _normalize_published_date(
            item.get("published_date") or item.get("date"),
            provider=provider,
            current_utc=current_utc,
        )
        accepted.append(
            SearchResult(
                title=title,
                snippet=snippet,
                url=url,
                provider=provider,
                query=query,
                published_date=published_date,
                rank=rank,
                score=score,
            )
        )
        if len(accepted) >= limit:
            break
    return tuple(accepted)


def _normalize_published_date(
    value: object,
    *,
    provider: SearchProvider,
    current_utc: datetime,
) -> str | None:
    text = str(value or "").strip()
    if not text or provider != "serper":
        return text or None

    reference = current_utc
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    else:
        reference = reference.astimezone(timezone.utc)
    lowered = text.casefold()
    if lowered in {"just now", "today"}:
        published = reference
    elif lowered == "yesterday":
        published = reference - timedelta(days=1)
    else:
        match = _RELATIVE_DATE.fullmatch(text)
        if match is None:
            return text
        raw_count = match.group("count").casefold()
        count = 1 if raw_count in {"a", "an", "one"} else int(raw_count)
        unit = match.group("unit").casefold()
        if unit.startswith("min"):
            delta = timedelta(minutes=count)
        elif unit in {"hr", "hour"}:
            delta = timedelta(hours=count)
        elif unit == "day":
            delta = timedelta(days=count)
        elif unit == "week":
            delta = timedelta(weeks=count)
        elif unit == "month":
            delta = timedelta(days=30 * count)
        else:
            delta = timedelta(days=365 * count)
        published = reference - delta
    return published.isoformat().replace("+00:00", "Z")


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed >= 1 else None


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and parsed not in {float("inf"), float("-inf")} else None


def _classify_status(provider: SearchProvider, status: int) -> _ClassifiedStatus:
    if status == 429:
        return _ClassifiedStatus(SearchOutcome.RATE_LIMITED, "provider rate limit reached", True)
    if status == 408:
        return _ClassifiedStatus(SearchOutcome.TIMEOUT, "provider request timed out", True)
    if status >= 500:
        return _ClassifiedStatus(SearchOutcome.SERVER_ERROR, "provider server failure", True)
    if status in {401, 403}:
        return _ClassifiedStatus(SearchOutcome.AUTH_ERROR, "provider authentication failed", False)
    if provider == "tavily" and status in {432, 433}:
        return _ClassifiedStatus(SearchOutcome.QUOTA_EXHAUSTED, "provider quota exhausted", False)
    return _ClassifiedStatus(SearchOutcome.REQUEST_ERROR, "provider rejected the request", False)


def _retry_delay(
    response: httpx.Response | None,
    attempt_number: int,
    remaining: float,
    *,
    current_utc: datetime,
) -> float | None:
    retry_after = (
        _parse_retry_after(response.headers.get("Retry-After"), current_utc=current_utc)
        if response is not None
        else None
    )
    if retry_after is not None:
        return retry_after if retry_after <= remaining else None
    return min(0.25 * (2 ** (attempt_number - 1)), 1.0, max(0.0, remaining))


def _parse_retry_after(value: str | None, *, current_utc: datetime) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            reference = current_utc
            if reference.tzinfo is None:
                reference = reference.replace(tzinfo=timezone.utc)
            seconds = (retry_at - reference).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return seconds if seconds >= 0 else None


def _trace_response(span: object | None, response: SearchResponse) -> None:
    if span is None:
        return
    attempts = [
        {
            "provider": attempt.provider,
            "outcome": attempt.outcome.value,
            "http_status": attempt.http_status,
            "retry_count": attempt.retry_count,
            "latency_ms": attempt.latency_ms,
            "raw_result_count": attempt.raw_result_count,
            "accepted_result_count": attempt.accepted_result_count,
        }
        for attempt in response.attempts
    ]
    try:
        span.update(
            output={
                "result_count": len(response.results),
                "selected_provider": response.selected_provider,
                "fallback_reason": response.fallback_reason,
                "attempts": attempts,
            }
        )
    except Exception as exc:  # pragma: no cover - tracing is always non-fatal
        logger.warning("search trace update failed (non-fatal): {}", type(exc).__name__)
