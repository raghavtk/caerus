# Caerus test suite

Tracks [#7](https://github.com/raghavtk/caerus/issues/7): figure out agent limits early without burning Gemini quota by accident.

## Two tiers

| Tier | What | Cost | How to run |
|------|------|------|------------|
| **Unit** (default) | Mocked / pure logic + fixture validators | Free | `pytest` |
| **Live eval** (opt-in) | Real Gemini (and search for company research) | Uses monthly quota | `CAERUS_ALLOW_LIVE=1` (below) |

Default `pytest` **never** runs live evals — they are deselected unless `CAERUS_ALLOW_LIVE=1`.

```bash
pip install -e ".[dev]"
pytest
```

---

## Layout

```
tests/
  README.md                 # this file
  conftest.py               # live-eval gate (deselects @pytest.mark.live without env)
  test_jd_parser.py         # unit: ATS detect + mocked parse
  test_resume_selector.py   # unit: heuristics + mocked select
  test_tracing.py           # unit: Langfuse session/spans (mocked)
  test_eval_harness.py      # unit: fixture pairing + soft-check helpers
  eval/
    test_agents_live.py     # live: per-agent soft checks against fixtures
  fixtures/
    jds/*.txt               # synthetic pasted job descriptions
    expectations/*.yaml     # soft expectations per fixture

skills/agent_eval.py        # shared harness used by live tests + `caerus eval`
.cursor/rules/no-live-evals.mdc
```

CLI entrypoints (see root `cli.py`):

- `caerus eval list` — list fixtures/agents (safe)
- `caerus eval run <agent> [--fixture ID]` — live eval (requires `CAERUS_ALLOW_LIVE=1`)

---

## Unit tests (safe / default)

No network, no Gemini. These always run with plain `pytest`.

### `test_jd_parser.py`

| Test | What it checks |
|------|----------------|
| `test_detect_ats_variants` | Greenhouse, Lever, Workday, Taleo, LinkedIn URL detection |
| `test_parse_jd_with_pasted_text` | Pasted text path with mocked `generate_structured` |

### `test_resume_selector.py`

| Test | What it checks |
|------|----------------|
| `test_heuristic_network_security` | `networking` → `NETWORK_SECURITY` |
| `test_heuristic_database` | `database` → `DATABASE` |
| `test_heuristic_ml` | `ml` → `AI_ML` |
| `test_heuristic_systems` | `systems` → `SYSTEMS` |
| `test_heuristic_ambiguous` | `backend` alone → no heuristic |
| `test_select_resume_with_llm` | LLM path mocked; heuristic can override `GENERAL` |

### `test_tracing.py`

Langfuse helpers with mocks: session ID format, MCP config JSON, nested pipeline spans, generation tracing, init failure cooldown, flush-on-update-failure, missing-keys verify.

### `test_eval_harness.py`

| Test | What it checks |
|------|----------------|
| `test_fixture_corpus_is_paired` | Every `jds/<id>.txt` has matching `expectations/<id>.yaml` |
| `test_check_jd_parser_soft_expectations` | Soft checks pass on a good `ParsedJD` |
| `test_check_resume_selector_accepts_allowed_variant` | Variant allow-list works |
| `test_check_cover_letter_flags_i_opener` | Cover letters starting with "I " fail the opener rule |

---

## Live evals (quota-aware)

Marked `@pytest.mark.live` in `tests/eval/test_agents_live.py`.  
Gated by `CAERUS_ALLOW_LIVE=1` (pytest deselects without it; CLI refuses without it).

```bash
# Cheapest poke (~1 Gemini call)
CAERUS_ALLOW_LIVE=1 caerus eval run jd_parser --fixture systems_cloudflare

# List fixtures / agents
caerus eval list

# Full live pytest suite (expensive — avoid unless intentional)
CAERUS_ALLOW_LIVE=1 pytest -m live -v
```

### Live tests

| Test | Fixtures | Agent under test |
|------|----------|------------------|
| `test_live_jd_parser` | all 5 | `jd_parser` |
| `test_live_resume_selector` | all 5 | `resume_selector` (includes a parse call) |
| `test_live_cover_letter` | all 5 | `cover_letter` (sparse fake brief; no search) |
| `test_live_company_research_smoke` | `systems_cloudflare` only | `company_research` (search-heavy) |

### Approximate Gemini cost per fixture

| Agent | Calls (rough) | Notes |
|-------|---------------|-------|
| `jd_parser` | 1 | Best starting point |
| `resume_selector` | 2 | parse + select |
| `cover_letter` | 3–4 | parse + select + letter + hook summary |
| `company_research` | 1 + up to 5 search queries | Smoke only on one fixture |

---

## Fixtures

Synthetic pasted JDs (not live URLs) so evals stay deterministic and offline-fetch-free.

| Fixture ID | Intent |
|------------|--------|
| `systems_cloudflare` | Systems / networking → SYSTEMS or NETWORK_SECURITY |
| `ml_anthropic` | ML / AI-infra → AI_ML |
| `networking_arista` | Networking / security → NETWORK_SECURITY or SYSTEMS |
| `database_neon` | Database internals → DATABASE (or SYSTEMS) |
| `ambiguous_backend` | Generic backend — parser should not invent niche signals |

Soft expectations (not brittle golden dumps) live in `tests/fixtures/expectations/<id>.yaml`:

- **jd_parser:** `company_contains`, `role_contains`, `domain_signals_any`, `seniority_in`
- **resume_selector:** `variant_in`, `grade_in`
- **cover_letter:** `max_words`, `min_paragraphs`, `forbid_opener_i`

### Adding a fixture

1. Add `tests/fixtures/jds/<id>.txt`
2. Add `tests/fixtures/expectations/<id>.yaml` with the same `id`
3. Run `pytest tests/test_eval_harness.py` (free) to confirm pairing
4. Optionally poke live: `CAERUS_ALLOW_LIVE=1 caerus eval run jd_parser --fixture <id>`

---

## Agent / automation policy

Cursor agents and CI must **not** set `CAERUS_ALLOW_LIVE` or run `pytest -m live` / `caerus eval run` unless the human explicitly asks in that turn. Quota is limited (~1000 requests/month).

See also `.cursor/rules/no-live-evals.mdc`.
