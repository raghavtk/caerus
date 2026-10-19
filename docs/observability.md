# Observe pipeline runs

Local metrics are available for every successful run without an external service.
Langfuse adds optional traces for the pipeline, agent stages, LLM generations,
search, and Notion calls.

## Enable Langfuse

Set these values in `.env`:

- `LANGFUSE_PUBLIC_KEY`.
- `LANGFUSE_SECRET_KEY`.
- `LANGFUSE_HOST` if you use a host other than `https://us.cloud.langfuse.com`.

Check connectivity, then run an application:

```sh
uv run --frozen caerus check
uv run --frozen caerus run "https://example.com/jobs/123" --no-notion
```

Replace the example URL with your job posting. When tracing is enabled, the
application summary includes a session ID and a link to the Langfuse trace.
Use the session ID to find observations for the run.

## Query traces from Cursor

The optional [Langfuse MCP server](https://langfuse.com/docs/api-and-data-platform/features/mcp-server)
lets an MCP client query traces. Generate the configuration from your `.env` keys:

```sh
uv run --frozen caerus langfuse mcp-config
```

Save the output to `.cursor/mcp.json`, or copy the
[configuration example](../.cursor/mcp.json.example) and replace the Basic auth
token. The file contains credentials and is gitignored.

Restart Cursor and confirm that the server connects. Ask it to list observations
for the session ID printed by a run, or to show generations under a trace ID.
The generated configuration also includes `langfuse-docs` for documentation queries.
