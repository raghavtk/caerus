import asyncio
from unittest.mock import patch

import httpx
import pytest

from agents.company_research import _ResearchDraft, research_company
from config import Settings
from schemas.models import ParsedJD, ResearchStatus
from skills.search import SearchOutcome, web_search


def settings(**changes):
    return Settings(_env_file=None, serper_api_key='secret', tavily_api_key='fallback-secret', **changes)


async def test_total_deadline_stops_request_and_prevents_fallback():
    calls = []
    stopped = asyncio.Event()

    async def handler(request):
        calls.append(request.url.host)
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        # Leave room for cold httpx imports on Windows CI before entering the handler.
        with patch('skills.search.get_settings', return_value=settings(search_total_timeout_seconds=0.3)):
            response = await web_search('query', client=client)
        assert not client.is_closed
    assert stopped.is_set()
    assert calls == ['google.serper.dev']
    assert response.attempts[0].outcome == SearchOutcome.TIMEOUT


@pytest.mark.parametrize('total_limited', [True, False])
async def test_early_timer_expiry_stops_only_the_exhausted_budget(total_limited):
    """Simulate a timer firing before the wall clock reaches the deadline."""
    calls = []

    class EarlyTimeout:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def expired(self):
            return True

    async def handler(request):
        calls.append(request.url.host)
        if request.url.host == 'google.serper.dev':
            raise TimeoutError('event-loop deadline expired early')
        return httpx.Response(200, json={'results': []})

    config = settings(search_timeout_seconds=1, search_total_timeout_seconds=1 if total_limited else 20)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with patch('skills.search.get_settings', return_value=config), patch(
            'skills.search.asyncio.timeout', side_effect=lambda _: EarlyTimeout()
        ):
            response = await web_search('query', client=client)

    # An expired provider budget must not cause a retry, even if wall time remains.
    assert calls.count('google.serper.dev') == 1
    assert response.attempts[0].outcome == SearchOutcome.TIMEOUT
    if total_limited:
        assert calls == ['google.serper.dev']
        assert response.fallback_reason is None
    else:
        assert calls == ['google.serper.dev', 'api.tavily.com']
        assert response.selected_provider == 'tavily'


@pytest.mark.parametrize('phase', ['request', 'retry', 'fallback'])
async def test_cancellation_propagates_without_retry_or_further_fallback(phase):
    entered = asyncio.Event()
    calls = []

    async def handler(request):
        calls.append(request.url.host)
        if phase == 'retry':
            return httpx.Response(429, headers={'Retry-After': '1'})
        if phase == 'fallback' and len(calls) == 1:
            return httpx.Response(401)
        entered.set()
        await asyncio.Event().wait()

    async def sleep(delay):
        entered.set()
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with patch('skills.search.get_settings', return_value=settings()):
            # Patch the injected sleep only for the retry case.
            from skills.search import _search_provider
            async def provider(*args, **kwargs):
                return await _search_provider(*args, **kwargs, sleep=sleep)
            with patch('skills.search._search_provider', side_effect=provider):
                task = asyncio.create_task(web_search('query', client=client))
                await asyncio.wait_for(entered.wait(), 1)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
        assert not client.is_closed
    assert len(calls) == (2 if phase == 'fallback' else 1)


@pytest.mark.parametrize('cancel', [False, True])
async def test_batch_cleans_up_active_and_queued_queries(cancel):
    entered = asyncio.Event()
    active = 0
    calls = 0
    clients = []

    async def search(query, num_results, *, client):
        nonlocal active, calls
        active += 1
        calls += 1
        clients.append(client)
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1

    config = settings(search_concurrency=1, research_search_timeout_seconds=0.03)
    with patch('agents.company_research.get_settings', return_value=config), patch(
        'agents.company_research.web_search', side_effect=search
    ), patch('agents.company_research.generate_structured') as generate:
        task = asyncio.create_task(research_company(ParsedJD(company='Acme', role='Engineer')))
        await asyncio.wait_for(entered.wait(), 1)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            brief = await task
            assert brief.research_status == ResearchStatus.UNAVAILABLE
            assert sum('deadline exhausted' in warning for warning in brief.concerns_or_unknowns) == 5
        generate.assert_not_called()
    assert calls == 1
    assert active == 0
    assert all(client.is_closed for client in clients)


async def test_research_integrates_shared_transport_fallback_and_synthesis():
    actual_client = httpx.AsyncClient
    seen_clients = []
    requests = []

    async def handler(request):
        requests.append(request.url.host)
        await asyncio.sleep(0)
        if request.url.host == 'google.serper.dev':
            return httpx.Response(503, headers={'Retry-After': '0'})
        return httpx.Response(200, json={'results': [{'title': 'Engineering', 'url': 'https://acme.test/engineering', 'content': 'Python platform'}]})

    def client_factory(**kwargs):
        client = actual_client(transport=httpx.MockTransport(handler), **kwargs)
        seen_clients.append(client)
        return client

    config = settings(search_max_attempts=1, search_concurrency=2)
    with patch('agents.company_research.httpx.AsyncClient', side_effect=client_factory), patch(
        'agents.company_research.get_settings', return_value=config
    ), patch('skills.search.get_settings', return_value=config), patch(
        'agents.company_research.get_user_profile', return_value={}
    ), patch('agents.company_research.generate_structured', return_value=_ResearchDraft()) as generate:
        brief = await research_company(ParsedJD(company='Acme', role='Engineer'))
    assert len(seen_clients) == 1
    assert seen_clients[0].is_closed
    assert requests.count('google.serper.dev') == 5
    assert requests.count('api.tavily.com') == 5
    assert len(brief.sources) == 1
    assert len(brief.sources[0].query_tags) == 5
    generate.assert_awaited_once()


async def test_cancelled_stream_is_closed():
    entered = asyncio.Event()

    class Stream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b'{"organic":'
            entered.set()
            await asyncio.Event().wait()

        async def aclose(self):
            self.closed = True

    stream = Stream()
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=stream)
    )) as client:
        with patch('skills.search.get_settings', return_value=settings()):
            task = asyncio.create_task(web_search('query', client=client))
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert stream.closed


async def test_batch_deadline_preserves_completed_evidence():
    from skills.search import SearchResponse, SearchResult

    async def search(query, num_results, *, client):
        if 'product engineering' in query:
            return SearchResponse((SearchResult('Acme', 'Python', 'https://acme.test', 'serper', query),), (), 'serper')
        await asyncio.Event().wait()

    with patch('agents.company_research.get_settings', return_value=settings(research_search_timeout_seconds=0.03)), patch(
        'agents.company_research.web_search', side_effect=search
    ), patch('agents.company_research.get_user_profile', return_value={}), patch(
        'agents.company_research.generate_structured', return_value=_ResearchDraft()
    ) as generate:
        brief = await research_company(ParsedJD(company='Acme', role='Engineer'))
    assert len(brief.sources) == 1
    assert brief.research_status == ResearchStatus.PARTIAL
    assert sum('deadline exhausted' in warning for warning in brief.concerns_or_unknowns) == 4
    generate.assert_awaited_once()


async def test_synthesis_cancellation_propagates():
    from skills.search import SearchResponse, SearchResult
    entered = asyncio.Event()

    async def generate(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    response = SearchResponse((SearchResult('Acme', 'Python', 'https://acme.test', 'serper', 'query'),), (), 'serper')
    with patch('agents.company_research.web_search', return_value=response), patch(
        'agents.company_research.get_user_profile', return_value={}
    ), patch('agents.company_research.generate_structured', side_effect=generate):
        task = asyncio.create_task(research_company(ParsedJD(company='Acme', role='Engineer')))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
