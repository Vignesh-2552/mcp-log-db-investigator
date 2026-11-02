# AGENTS.md

Instructions for AI coding agents (Claude Code, Cursor, Codex, Copilot, etc.) working in this
repository. Read this first, then [`CLAUDE.md`](CLAUDE.md) for the full architecture notes and
[`docs/code-structure.md`](docs/code-structure.md) for the file-by-file map.

## Project in one paragraph

`opsinsight-mcp` is a [FastMCP](https://gofastmcp.com) server that gives an AI client safe,
**read-only** tools for investigating support tickets across **PostgreSQL**, **AWS CloudWatch**
and **New Relic**. The client model writes the queries; this server supplies grounding context
(schema, field discovery, a query cookbook), validates every query with a guardrail, runs it,
redacts PII and returns a structured response.

## Hard rules (never break these)

1. **Read-only, always.** No tool may write to a database, change AWS/New Relic state, or skip
   a guardrail. A change that weakens this needs an issue and a human decision first.
2. **Respect the layering.** `tools/*` → `service/*` → `integrations/<source>/guardrail.py` →
   `integrations/<source>/client.py | engine.py`. Calls only go downward. No layer skipping.
3. **Tool files hold no logic.** A file in `tools/<source>/` is the `@mcp.tool()` decorator, the
   docstring, the parameters, and one line calling the service. Logic goes on the service class.
4. **Guardrails are pure.** No network, no DB, no file I/O in `guardrail.py`. Every user-supplied
   query passes through its guardrail before execution.
5. **Redact before returning.** Any tool returning rows or log text calls `redact_rows` /
   `redact_log_event` (`core/redaction.py`), and redacts *before* truncating (`_cap_rows`).
6. **Keep the response shape.** Success: `{"ok": True, "data": {...}, "meta": {...}}` via
   `BaseService.ok`. Failure: a `ToolError` subclass converted with `.to_response()`, carrying
   a machine-readable `rule` and a `detail` that tells the model what to change.
7. **Shared helpers go in `core/`.** A service must not import another source's
   `integrations/*` module; move source-agnostic code to `core/` instead.
8. **Never commit secrets.** `.env` is local only; add new settings to `.env.example` with a safe
   placeholder.

## Commands

```powershell
uv sync                                         # install deps
uv run ruff check .                             # lint (must be clean)
uv run pytest -m "not integration" -q         # unit tests, no external services
uv run pytest -m "not integration" --cov=opsinsight  # coverage (CI fails under 85%)
uv run pytest -m integration                    # needs Postgres; auto-skips otherwise
uv run investigation-server                     # HTTP server (:8001/mcp by default)
uv run opsinsight-mcp                           # stdio server
```

Before saying a task is done: `ruff check .` is clean and `pytest -m "not integration"` passes.

## CI gate: every test must pass

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push to `main`/`dev` and
every pull request. **The build succeeds only when all of these pass; any one failing fails
the whole run:**

1. `uv run ruff check .` — lint must be clean.
2. `uv run pytest -m "not integration" --cov=opsinsight` — every unit test must pass **and**
   coverage must stay at or above 85% (`fail_under` in `pyproject.toml`).
3. `uv run pytest -m integration` — integration tests must pass (they skip only when no
   Postgres is reachable).
4. `docker build` — runs **only after** steps 1–3 pass (`needs: lint-and-test`).

A red CI run means the PR cannot be merged. Never make it green by deleting, skipping
(`@pytest.mark.skip`, `-k` filters), or loosening a test or assertion, lowering `fail_under`,
or editing `ci.yml` to drop a step. Fix the code, or fix the test if it is wrong and say why.
Run steps 1–2 locally before pushing.

## Common tasks — checklists

### Add a new tool

1. Add the logic as a method on the source's service class (`service/<source>_service.py`).
   Validate through the guardrail, execute, redact, cap, return `self.ok(...)`; catch
   `ToolError` → `.to_response()` and the source's exception family → its `_*_error_response`.
2. DB methods must run inside `async with self._query_slot():`.
3. Create `tools/<source>/<tool_name>.py` (copy `tools/newrelic/nr_list_event_types.py`).
4. Import it in `tools/<source>/__init__.py` and add it to `__all__`, or it never registers.
5. Add the tool name to `EXPECTED_TOOLS` in `src/opsinsight/core/audit_test.py` after reviewing it for
   authorization, redaction and audit.
6. Add unit tests that construct the service with an injected fake client (no monkeypatching).
7. Document it in `docs/tool-reference.md`, and in `README.md` / `docs/design.md` §4 if user-facing.
8. If it introduces a useful query pattern, add an entry to `src/opsinsight/data/query_cookbook.yaml`.

### Add a source to `trace_request`

Write one adapter class implementing the `TraceSource` Protocol in `service/trace_sources.py`
(`name`, `skip_reason`, `planned_query`, `start`, `collect`) and add it to
`default_trace_sources()`. Do not add source-specific branches to `TraceService`. Also add the
name to the `sources` `Literal` in `tools/trace/trace_request.py`.

### Add a setting

1. Add the field to `Settings` in `core/config.py`.
2. Add it to `.env.example` with a comment.
3. Tests that change env vars call `get_settings.cache_clear()` and `reset_container()` (use the
   `settings_override` fixture in `src/opsinsight/conftest.py`).

### Change a guardrail

Add tests for both the newly allowed and the still-rejected cases in
`src/opsinsight/integrations/<source>/guardrail_test.py`. Guardrail changes are security changes — mention the
risk in the PR description.

## Testing conventions

- **Tests live beside the code**: `foo.py` → `foo_test.py` in the same folder. There is no
  `tests/` folder. Extra test files for one module: `foo_<topic>_test.py`; tests needing real
  Postgres: `foo_integration_test.py` marked `@pytest.mark.integration`.
- `asyncio_mode = "auto"`: async tests need no decorator.
- Inject fakes through constructors: `DatabaseService(settings, engine_factory=..., catalog=...)`,
  `CloudWatchLogsService(settings, logs_client_factory=...)`,
  `CloudWatchMetricsService(settings, metrics_client_factory=...)`,
  `NewRelicService(settings, run_nrql_fn=...)`, `TraceService(settings, [sources...])`,
  or a full `Container(...)`. Injected types are Protocols in `service/ports.py`.
- Reset caches between tests that change config: `get_settings.cache_clear()`,
  `reset_container()`, `reset_engine()`, `reset_clients()`.
- Unit tests must not need network, AWS, New Relic, or Postgres.

## Git workflow

- Never commit to `main` directly. Branch as `<type>/<short-kebab-description>`, e.g.
  `feat/nr-metric-tool`, `fix/cw-window-clamp`, `docs/agents-md-and-code-structure`.
- [Conventional Commits](https://www.conventionalcommits.org/): `feat(scope): ...`,
  `fix(scope): ...`, `test: ...`, `docs: ...`, `refactor: ...`, `chore: ...`.
- Update `CHANGELOG.md` for user-visible changes.

## Things that do not exist (do not assume them)

`investigation_start`, and the MCP prompts `investigate_incident`, `trace_user_journey`,
`slow_endpoint_rca` are **not implemented**. See `docs/design.md` §4.5 before building on them.

## Keep docs in sync

If you change structure or conventions, update the matching doc in the same PR — see the
"Keeping this document current" table in [`docs/code-structure.md`](docs/code-structure.md).
