# Caerus

Caerus is a command-line tool that prepares a job application package from a job
URL or pasted description. It researches the company, selects a resume from your
existing variants, and writes a cover letter with Gemini.

Each live run produces a company brief, a resume selection report, and an editable
DOCX cover letter with a one-page PDF. Caerus can also log the application to Notion.

## Try the offline demo

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run:

```sh
git clone https://github.com/raghavtk/caerus.git
cd caerus
uv python install 3.12
uv sync --locked --extra dev --python 3.12
uv run --frozen caerus demo
```

The command prints a metrics summary and the path to a new directory under
`outputs/`. Open that directory to read the company brief, resume report, and
Markdown cover letter. It also contains the synthetic profile, saved job input,
recorded responses, `application.json`, and `metrics.json`.

The demo replays fictional responses through the pipeline. It makes no API calls
and needs no credentials, personal files, LibreOffice, or Poppler. It demonstrates
the application flow and output files. To inspect the saved text artifacts before
running it, see [sample outputs](examples/demo).

To choose the output location, run:

```sh
uv run --frozen caerus demo --output-dir outputs/my-demo
```

## Prepare a real application

Live runs need a Gemini API key, a search provider, your profile and resumes, and
the document tools described below. They use API quota.

1. Copy [`.env.example`](.env.example) to `.env`. Set `GEMINI_API_KEY` and at least
   one of `SERPER_API_KEY` or `TAVILY_API_KEY`. When both search keys are present,
   Caerus tries Serper first by default.
2. Copy [the profile example](context/user_profile.example.yaml) to
   `context/user_profile.yaml`. Replace the example identity, experience, and projects
   with your information.
3. Place your resume PDFs in `resumes/`. Set each `resume_variants` entry in your
   profile to the matching filename. Caerus selects an existing resume.
4. Install LibreOffice and Poppler. Make `soffice` and `pdftoppm` available on your
   system path. If Caerus cannot find LibreOffice, set `LIBREOFFICE_PATH` in `.env`.
5. Run the setup check, then generate an application:

```sh
uv run --frozen caerus check
uv run --frozen caerus run "https://example.com/jobs/123" --no-notion
```

Replace the example URL with a real job posting. You can pass quoted job-description
text instead. The setup check reports optional services too. Missing Notion or
Langfuse credentials do not block a run with `--no-notion`.

The application summary prints the output directory. It contains:

- `cover_letter.docx` and `cover_letter.pdf`.
- `company_brief.md` and `resume_report.md`.
- A copy of the selected resume, when the file exists.
- `metrics.json` with per-stage measurements.

The profile example explains how to name resume variants and provide project
evidence. Optional `COVER_LETTER_*` values in `.env` add contact details to the
letter header. Blank values are omitted. Personal profiles, resumes, credentials,
and runtime output directories are gitignored.

## Read run metrics

The CLI prints each stage's duration, request count, input tokens, output tokens,
and estimated LLM cost. Successful runs save the same measurements in `metrics.json`.
Local metrics work without Langfuse.

To estimate token cost, set `LLM_INPUT_USD_PER_MILLION` and
`LLM_OUTPUT_USD_PER_MILLION` to the rates for your configured `GEMINI_MODEL`.
If rates or usage metadata are missing, the estimate is `unknown` in the CLI and
`null` in JSON. Demo runs report zero requests, tokens, and cost.

Counts include explicit Gemini calls, search attempts, job-page fetches, and Notion
calls. Search retries and fallback attempts count separately. SDK-internal retries,
HTTP redirects, and tracing traffic are excluded. Output token counts include
Gemini thinking tokens. Cost estimates exclude search charges, other services,
cached-token discounts, and pricing tiers.

## Resume an interrupted run

Pipeline stages are saved locally so failed runs can resume without regenerating
completed work. Use `caerus run --resume "outputs/.runs/<session>/state.json"`.
See [pipeline recovery](docs/pipeline-recovery.md) for guarantees and limitations.

## Connect optional services

To log applications in Notion, configure `NOTION_TOKEN` and `NOTION_DATABASE_ID`,
then omit `--no-notion` from the run command.

To trace runs in Langfuse, follow the [observability guide](docs/observability.md).
It covers credentials, trace links, session IDs, and the optional Cursor MCP setup.

## Develop and test

```sh
uv run --frozen --extra dev pytest -q
```

By default, tests use fixtures and mocks without calling Gemini. CI checks the
locked installation, tests, demo, and package build on Ubuntu and Windows with
Python 3.11 and 3.12.

See the [development guide](docs/development.md) for dependency updates and the
async Python API. See the [test guide](tests/README.md) for opt-in live evals.
