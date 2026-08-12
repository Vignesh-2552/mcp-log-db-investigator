# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A [FastMCP](https://gofastmcp.com) server exposing safe, **read-only** tools for investigating
support tickets: schema discovery + query execution against Postgres, log/metric access
against AWS CloudWatch, and log/event access against New Relic (NRQL). It's meant to be
driven by a client model (Claude Desktop, Cursor) that writes queries in natural-language
conversation — the server's job is to supply enough grounding context (schema, field
discovery, a query cookbook) that the model generates correct queries, and to validate +
execute them safely. See `docs/design.md` for the full design rationale (§3.1 explains why
query generation is client-side, not server-side).

**`docs/design.md` is a draft design doc, not a changelog** — §4.3 describes composite tools
`investigation_start` and `trace_request` that are **not implemented**; don't assume they
exist. Cross-source correlation (DB + CloudWatch + New Relic) is currently manual, driven by
the `investigate_incident`/`trace_user_journey`/`slow_endpoint_rca` prompts in
`src/prompts/investigation.py`, which walk the model through fanning out to each source and
merging results itself.

## Commands

```powershell
uv sync                                          # install deps
copy .env.example .env                           # then fill in DB_URL, AWS_*, NEW_RELIC_*
uv run pytest tests/unit                         # guardrail/redaction tests, no external deps
uv run pytest tests/unit/test_db_guardrail.py -q # run a single test file
uv run pytest tests/unit -k test_limit_clamped    # run a single test by name
uv run pytest tests/integration -m integration    # requires a reachable Postgres; auto-skips otherwise
uv run ruff check .                              # lint
uv run investigation-server                      # start the MCP server (streamable HTTP, :8000/mcp by default)
```

`asyncio_mode = "auto"` is set in `pyproject.toml` — async test functions run without needing
`@pytest.mark.asyncio`.

## Architecture

**Layering is strict and one-directional**: `tools/*` (the `@mcp.tool()`-decorated functions
the client calls) → `integrations/<source>/guardrail.py` (validate/reject, pure functions,
no I/O) → `integrations/<source>/client.py` or `engine.py` (actual network/DB I/O). Guardrail
functions never touch the network; tool functions never build a query without passing it
through the guardrail first. When adding a tool, follow this shape — see `newrelic_tools.py`
for the smallest complete example (guardrail → client → redact → structured response).

**Three independent data sources, one shared pattern**: `database` (SQLAlchemy/asyncpg +
`sqlglot` AST validation), `cloudwatch` (boto3 Logs Insights + a log-group allowlist + a
bytes-scanned cost ceiling), `newrelic` (NerdGraph GraphQL + a regex-based NRQL guardrail,
since NRQL has no AST parser like sqlglot). Each source has a `*_describe_*`/`*_list_*`
grounding tool that must be called before writing a query against unfamiliar fields:
`db_describe_table`, `cw_describe_log_fields`, `nr_describe_log_fields` (uses NRQL's
`keyset()` and separately flags likely trace/correlation-id attributes). Skipping the
grounding step is the main cause of queries that silently return zero rows because a field
name was guessed wrong.

**`core/app.py` holds the single shared `mcp = FastMCP(...)` instance in its own module**,
imported by every tool/resource/prompt module as `from core.app import mcp`. This exists
specifically so `server.py` (which is also the entry point run via `-m`/`investigation-server`)
never gets circularly imported — see the comment in `core/app.py` if touching this.

**Registration is import-side-effect based**: `src/tools/__init__.py`,
`src/resources/__init__.py`, `src/prompts/__init__.py` each import their submodules purely
for the `@mcp.tool()`/`@mcp.resource()`/`@mcp.prompt()` decorators to run. `server.py` imports
all three `__init__` modules before calling `mcp.run()`. A new tool/resource/prompt file must
be added to the relevant `__init__.py` import list or it will never register.

**Errors are structured and self-correcting by design**: every guardrail raises a `ToolError`
subclass (`GuardrailError`, `CWGuardrailError`, `NewRelicGuardrailError` in `core/errors.py`)
carrying a machine-readable `rule` plus a `detail` string explaining what to change — never a
bare "denied". Every `@mcp.tool()` function catches its own `ToolError`s and converts them via
`.to_response()` into `{"ok": False, "error": {...}}`; successful results always follow
`{"ok": True, "data": {...}, "meta": {...}}`. Preserve this shape in any new tool.

**Settings are one `pydantic-settings` object** (`core/config.py`), loaded once via
`@lru_cache def get_settings()`. Tests that change env vars must call
`get_settings.cache_clear()` (and, for DB tests, `await reset_engine()` from
`integrations/database/engine.py`) — see `tests/conftest.py`'s `settings_override` fixture
and `tests/unit/test_newrelic_tools.py` for the pattern.

**Redaction is centralized and applied at the tool boundary**, not deeper in the stack:
`core/redaction.py` masks PII by column name (`PII_COLUMN_NAMES`) and by regex pattern
(email/card/Aadhaar/PAN/phone) applied to both DB row values and raw log message text.
Every tool that returns rows/log content calls `redact_rows`/`redact_log_event` right before
building its response — do the same in new tools rather than relying on upstream masking.

**The query cookbook (`src/data/query_cookbook.yaml`, exposed as the `docs://query-cookbook`
resource) is a deliberate accuracy lever**, not just documentation — per design doc §4.4,
giving the client model curated example queries measurably improves generation accuracy over
schema alone. When a query pattern proves useful or a source's field-naming quirk causes
repeated bad queries, add/update a cookbook entry rather than only fixing it in prose.

**CloudWatch cost/window safety**: `cw_run_insights_query` enforces a time-window cap
(`clamp_window`), polls with exponential backoff (`poll_query_with_backoff`), and aborts +
reports if `bytesScanned` exceeds `CW_MAX_BYTES_SCANNED` — bytes scanned is always logged and
returned in `meta`, since Logs Insights bills per GB scanned.

**DB safety is defense-in-depth**: `sqlglot`-based AST validation (single `SELECT`/`WITH...SELECT`
only, blocked node types, blocked dangerous functions, LIMIT injection/clamping) in
`integrations/database/guardrail.py`, *plus* session-level hardening applied on every new
physical connection in `integrations/database/engine.py` (`SET default_transaction_read_only
= on`, statement timeout, idle-in-transaction timeout) — the DB role itself is meant to be the
last line of defense, not the application layer alone.
