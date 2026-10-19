# Development

## Install and test

Install Python 3.11 or 3.12 and uv, then run:

```sh
uv sync --locked --extra dev --python 3.12
uv run --frozen --extra dev pytest -q
uv run --frozen caerus demo
uv build --python 3.12
```

CI runs these checks on Ubuntu and Windows with Python 3.11 and 3.12. It uploads
the generated demo files as workflow artifacts. Live evals require an explicit
opt-in. See the [test guide](../tests/README.md) for fixture and eval commands.

## Update dependencies

`uv.lock` records dependency requirements and resolved versions. After changing
`pyproject.toml`, or merging a branch that changes dependencies, run:

```sh
uv lock
uv lock --check
uv sync --locked --extra dev --python 3.12
```

Commit `pyproject.toml` and `uv.lock` together when requirements change. A locked
install fails if their metadata differs, even when the resolved package version
already satisfies the new requirement. Keep `--locked` in CI so it detects drift.

## Async search and research


`web_search`, `research_company`, `orchestrate`, and `run_agent_eval` are async
functions. Await them in applications and notebooks that already have an event
loop. The synchronous Typer commands own the `asyncio.run(...)` boundary.

```python
import httpx
from skills.search import web_search
from agents.company_research import research_company
from schemas.models import ParsedJD

async def investigate():
    async with httpx.AsyncClient(follow_redirects=False) as client:
        response = await web_search("Acme engineering", client=client)
    brief = await research_company(ParsedJD(company="Acme", role="Engineer"))
    return response, brief
```

Search creates and closes an async client when none is supplied; caller-owned
clients remain open. Research shares one client across its query batch. Results
and source IDs follow query order regardless of completion order; category-aware
ranking and typed `SearchResponse` diagnostics remain in place.

- `SEARCH_CONCURRENCY` (default 3, range 1â€“5) bounds active research queries.
- `SEARCH_TIMEOUT_SECONDS` (default 10) bounds each provider, including retries,
  streaming body reads, and retry delays.
- `SEARCH_TOTAL_TIMEOUT_SECONDS` (default 20) is the shared query budget across
  providers and fallback. A spent budget stops fallback and returns diagnostics.
- `RESEARCH_SEARCH_TIMEOUT_SECONDS` (default 60) bounds the whole search batch,
  including time waiting for a concurrency slot. On expiry, unfinished queries
  are cancelled and awaited; completed evidence remains available for synthesis
  and missing coverage is reported. This deadline applies to search, not synthesis.

Caller cancellation propagates as `asyncio.CancelledError`, closes owned clients
and active response streams, and cancels and awaits outstanding batch tasks.
Other application stages (JD parsing, resume selection and cover letter generation, output and
Notion writing) retain their existing synchronous APIs within orchestration.
Use `asyncio.timeout(...)` around research when synthesis also needs a deadline.
Offline async tests use `pytest-asyncio` in auto mode. Live tests remain opt-in.
