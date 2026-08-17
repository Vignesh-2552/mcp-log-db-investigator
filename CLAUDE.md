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
`investigation_start` and `trace_request`, and §4.4 describes MCP prompts
(`investigate_incident`/`trace_user_journey`/`slow_endpoint_rca`); **none of these are
implemented**; don't assume they exist. Cross-source correlation (DB + CloudWatch + New
Relic) is currently entirely manual — the client model fans out to each source's tools and
merges results itself, with no server-side guidance beyond the query cookbook.

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

**Layering is strict and one-directional**: `tools/*` (thin `@mcp.tool()`-decorated wrappers)
→ `service/*` (business logic, one class per source) → `integrations/<source>/guardrail.py`
(validate/reject, pure functions, no I/O) → `integrations/<source>/client.py` or `engine.py`
(actual network/DB I/O). Guardrail functions never touch the network; service methods never
build a query without passing it through the guardrail first.

**`src/service/` holds one class per source** — `DatabaseService`, `CloudWatchService`,
`NewRelicService` (`service/database_service.py`, `service/cloudwatch_service.py`,
`service/newrelic_service.py`), all inheriting `BaseService` (`service/base.py`) for the
shared `Settings` handle and the `ok(data, meta)` success-envelope helper. This is where a
tool's actual logic (validate → execute → redact → structured response) lives; error-response
translation for that source's exception family (`SQLAlchemyError` /
`BotoCoreError`+`ClientError` / `httpx.HTTPError`) is a private method on the same class
(`_sqlalchemy_error_response`, `_aws_error_response`, `_httpx_error_response`) rather than a
shared abstract method, since the three sources don't share an exception type — forcing one
would violate interface segregation for no benefit. Cross-tool logic that one source's several
tools share (e.g. CloudWatch's `run_insights_query`, used by both `cw_run_insights_query` and
`cw_get_trace_events`) is just another method on that source's service class, called from the
other public methods — composition over duplicating the polling/cost-ceiling logic per tool.
Network/DB clients are **constructor-injected** on `CloudWatchService`
(`logs_client_factory`/`metrics_client_factory`, default the real boto3 factories) and
`NewRelicService` (`run_nrql_fn`, defaults the real NerdGraph client) — dependency inversion,
so unit tests pass a fake client into the constructor instead of monkeypatching module state;
`DatabaseService` has no such seam because its only unit-level tests are integration tests
against a real Postgres (see `tests/integration/test_db_tools.py`).

**`tools/*` are thin MCP wrappers, nothing else**: each source is its own package
(`tools/database/`, `tools/cloudwatch/`, `tools/newrelic/`) with **one file per tool**, named
after the tool itself (e.g. `tools/database/db_resolve_store.py`). A tool file's only job is
the `@mcp.tool()` decorator, the docstring the client model reads, and the parameter schema —
the body is one line pulling that source's service off the container and calling the matching
method (see `tools/newrelic/nr_list_event_types.py` for the smallest complete example). Do not
put logic in a tool file; if you're tempted to, it belongs on the service class instead. The
one deliberate exception is inventory tools (`db_list_tables`, `cw_list_log_groups`,
`nr_list_event_types`): their service methods send a hardcoded, parameter-free (or
window-clamped) query straight to the client, bypassing the query guardrail, since there's no
user-supplied query text to validate — `nr_list_event_types` in particular sends `SHOW EVENT
TYPES`, which isn't a `SELECT` and would otherwise be rejected by `validate_nrql`.

**`core/container.py` is the composition root** — the one place that constructs services and
wires their dependencies. `Container` lazily builds and caches (singleton-scoped, per
container instance) `database_service`/`cloudwatch_service`/`newrelic_service`, each backed by
`self.settings` (which resolves through `get_settings()` unless a `Settings` override was
passed to the constructor). Every dependency — `settings`, or a fully pre-built service — can
also be injected via `Container(...)` keyword arguments; tests that want an isolated object
graph build their own `Container` rather than reaching for the process-wide one.
`get_container()` returns the process-wide singleton that `tools/*` call
(`get_container().database_service.list_tables(...)`); `reset_container()` drops it so the
next `get_container()` rebuilds against current `Settings` — call it after
`get_settings.cache_clear()` in any test that routes through the container, the same way
`reset_engine()` is called for the DB engine (`tests/conftest.py`'s `settings_override` does
both). This container is *not* the seam individual service unit tests use — those construct
the service directly with an injected fake client (see below) since that's simpler when you
only need one service; the container exists for wiring the full tools → services graph in
`server.py` and for tests that want that whole graph pre-assembled.

**Three independent data sources, one shared pattern**: `database` (SQLAlchemy/asyncpg +
`sqlglot` AST validation), `cloudwatch` (boto3 Logs Insights + a log-group allowlist + a
bytes-scanned cost ceiling), `newrelic` (NerdGraph GraphQL + a regex-based NRQL guardrail,
since NRQL has no AST parser like sqlglot). Each source has a `*_list_*` inventory tool
(`db_list_tables`, `cw_list_log_groups`, `nr_list_event_types`) and a `*_describe_*`
grounding tool (`db_describe_table`, `cw_describe_log_fields`, `nr_describe_log_fields`, which
uses NRQL's `keyset()` and separately flags likely trace/correlation-id attributes) that
should be called before writing a query against unfamiliar fields. Skipping the
grounding step is the main cause of queries that silently return zero rows because a field
name was guessed wrong.

**`core/app.py` holds the single shared `mcp = FastMCP(...)` instance in its own module**,
imported by every tool/resource/prompt module as `from core.app import mcp`. This exists
specifically so `server.py` (which is also the entry point run via `-m`/`investigation-server`)
never gets circularly imported — see the comment in `core/app.py` if touching this.

**Registration is import-side-effect based**: `src/tools/__init__.py` imports the three
source packages (`cloudwatch`, `database`, `newrelic`); each package's own `__init__.py` in
turn imports every tool file in it purely for the `@mcp.tool()` decorator to run, and
re-exports the tool function so it can also be imported as `from tools.<source> import
<tool_name>`. `src/resources/__init__.py` does the same for `@mcp.resource()`. `server.py`
imports both top-level `__init__` modules before calling `mcp.run()`. A new tool file must be
added to its source package's `__init__.py` import list (and, transitively, the source
package must already be listed in `src/tools/__init__.py`) or it will never register.

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
and `tests/unit/test_newrelic_tools.py` for the pattern. Unit tests construct the service
class directly (`CloudWatchService(get_settings(), logs_client_factory=lambda s: fake_client)`,
`NewRelicService(get_settings(), run_nrql_fn=fake_run_nrql)`) rather than monkeypatching —
prefer that constructor-injection seam over `monkeypatch.setattr` when a new service method
needs a fake client/network call in tests.

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
