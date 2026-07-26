# Caerus

God of opportunity, luck, and favorable moments.

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
