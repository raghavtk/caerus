# Caerus test suite

Tracks [#7](https://github.com/raghavtk/caerus/issues/7): figure out agent limits early without burning Gemini quota by accident.

## Two tiers

| Tier | What | Cost | How to run |
|------|------|------|------------|
| **Unit** (default) | Mocked / pure logic + fixture validators | Free | `pytest` |
| **Live eval** (opt-in) | Real Gemini (and search for company research) | Uses monthly quota | Double-gated (below) |

Default `pytest` **never** runs live evals — they are deselected unless `CAERUS_ALLOW_LIVE=1`.

## Unit tests (safe / default)

```bash
pip install -e ".[dev]"
pytest
```

These cover ATS detection, resume heuristics, fixture/expectation pairing, and soft-check helpers. No network, no Gemini.

## Live evals (quota-aware)

Live tests are gated by env var `CAERUS_ALLOW_LIVE=1` (CLI refuses without it; pytest deselects live tests without it).

```bash
# One agent, one fixture (cheapest way to poke a limit)
CAERUS_ALLOW_LIVE=1 caerus eval run jd_parser --fixture systems_cloudflare

# List fixtures / agents
caerus eval list

# Full live pytest suite (expensive — avoid unless intentional)
CAERUS_ALLOW_LIVE=1 pytest -m live -v
```

### Approximate Gemini cost per fixture

| Agent | Calls (rough) | Notes |
|-------|---------------|-------|
| `jd_parser` | 1 | Best starting point |
| `resume_selector` | 2 | parse + select |
| `cover_letter` | 3–4 | parse + select + letter + hook summary |
| `company_research` | 1 + search | Smoke-tested on one fixture only |

### Fixtures

Synthetic pasted JDs live in `tests/fixtures/jds/`. Soft expectations (not brittle golden outputs) live in `tests/fixtures/expectations/`.

Add a new case by pairing:

1. `tests/fixtures/jds/<id>.txt`
2. `tests/fixtures/expectations/<id>.yaml`

## Agent / automation policy

Cursor agents and CI must **not** set `CAERUS_ALLOW_LIVE` or run `pytest -m live` / `caerus eval run` unless the human explicitly asks in that turn. Quota is limited (~1000 requests/month).
