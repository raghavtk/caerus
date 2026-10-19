# Caerus

God of opportunity, luck, and favorable moments.

## Clean installation and offline demo

Install Python 3.11+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).
From a clean checkout, on Windows, macOS or Linux:

```sh
uv sync --locked --extra dev
uv run --frozen caerus demo
uv run --frozen --extra dev pytest -q
```

`uv.lock` pins direct and transitive dependencies. CI checks the locked install,
offline tests, demo and wheel build on Windows and Linux with Python 3.11/3.12.
Use `uv lock` to intentionally update dependencies and commit the changed lock.

The demo needs no `.env`, API keys, personal profile, resume, LibreOffice or
Poppler. It replays bundled synthetic stage responses through the orchestrator;
it demonstrates pipeline wiring and output generation, not live model quality.
Even if credentials exist locally, the demo never loads settings or enables tracing.
Run `uv run --frozen caerus demo --output-dir outputs/my-demo` to choose a destination.
Each run creates a new directory with a fictional candidate profile, saved job and
responses, company brief, resume report, Markdown letter, `application.json`, and
`metrics.json`. Demo letters use Markdown; live runs retain DOCX/PDF rendering.
See [sample outputs](examples/demo) for the saved text artifacts.

For live runs, copy `.env.example` to `.env`, configure Gemini and a search provider,
and copy `context/user_profile.example.yaml` to `context/user_profile.yaml` with
your information. Add resumes under `resumes/` and install the document tools below.
Use `uv run caerus run "JOB_URL_OR_TEXT" --no-notion` to omit optional Notion sync.

## Local metrics

Every successful pipeline run prints stage latency, request counts, input/output
tokens and estimated USD, and saves `metrics.json` in its output directory.
Metrics work independently of Langfuse. Counts include explicit Gemini calls and
search attempts (including retries/fallback), job-page fetches and Notion API calls.
SDK-internal retries, HTTP redirects and tracing traffic are not counted. Async search tasks
share their parent stage counters; concurrent runs have separate counters.

Token totals come from Gemini usage metadata, including thinking tokens in output.
Set `LLM_INPUT_USD_PER_MILLION` and `LLM_OUTPUT_USD_PER_MILLION` to the applicable
rates for your configured model. No current prices are assumed. Estimates exclude
search, Notion and infrastructure costs, cached-token discounts and pricing tiers.
Missing usage or rates yields `null` (displayed as `unknown`), not a free request.
Offline replay makes zero requests, consumes zero tokens and has zero estimated cost.
Metrics contain no prompts, profile content or credentials. Latencies vary per run.

## Document output prerequisites

Cover letters are written as editable DOCX files and converted to one-page PDFs. Install LibreOffice
(`soffice`) and Poppler (`pdftoppm`) on the runtime host. If LibreOffice is installed outside the system
path, set `LIBREOFFICE_PATH` in `.env`. Run `caerus check` to verify both tools before generating an
application package.

Optional cover-letter contact fields are configured with `COVER_LETTER_EMAIL`,
`COVER_LETTER_PHONE`, `COVER_LETTER_LOCATION`, `COVER_LETTER_LINKEDIN_URL`, and
`COVER_LETTER_PORTFOLIO_URL`; blank values are omitted from the document header.

## Testing

See [tests/README.md](tests/README.md) for the dual-tier suite (free unit tests vs opt-in live agent evals). Default `pytest` never hits Gemini.

## Observability

Caerus emits optional Langfuse traces for each pipeline run. Traces are hierarchical: a root `caerus.pipeline` span groups agent step spans, LLM generations, and tool calls (web search, Notion sync).

### Setup

1. Copy [`.env.example`](.env.example) to `.env` and set:
   - `LANGFUSE_PUBLIC_KEY`
   - `LANGFUSE_SECRET_KEY`
   - `LANGFUSE_HOST` (default: `https://us.cloud.langfuse.com`)
2. Verify connectivity:

```bash
caerus check
```

3. Run the pipeline — the summary table includes **Session ID** and **Langfuse Trace** URL when tracing is enabled:

```bash
caerus run "https://example.com/jobs/123"
```

### Langfuse MCP (Cursor)

Query traces from Cursor using the [Langfuse MCP server](https://langfuse.com/docs/api-and-data-platform/features/mcp-server):

1. Generate a ready-to-paste config from your `.env` keys:

```bash
caerus langfuse mcp-config
```

2. Save the output to `.cursor/mcp.json` (gitignored), or copy [`.cursor/mcp.json.example`](.cursor/mcp.json.example) and replace the Basic auth token.
3. Restart Cursor and confirm the Langfuse MCP server shows as connected.

Example MCP prompts:

- "List observations for session `<session-id-from-run>`"
- "Show generations under trace `<trace-id>`"
- "Find recent observations named `caerus.pipeline`"

The config also includes **langfuse-docs** MCP (`https://langfuse.com/api/mcp`) for SDK documentation lookups while developing agents.

### Session IDs

Each run gets a session ID like `caerus-20260726T021500Z-a1b2c3d4`, derived from the input and timestamp. Use this ID to filter observations in Langfuse or via MCP.


### Async search and research API

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

- `SEARCH_CONCURRENCY` (default 3, range 1–5) bounds active research queries.
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
Other application stages (JD parsing, resume/cover letter generation, output and
Notion writing) retain their existing synchronous APIs within orchestration.
Use `asyncio.timeout(...)` around research when synthesis also needs a deadline.
Offline async tests use `pytest-asyncio` in auto mode. Live tests remain opt-in.
