---
name: add-mcp-tool
description: Add a new @mcp.tool() to this FastMCP investigation server (database/cloudwatch/newrelic source) following the project's layered tools → service → guardrail → queries/constants/models convention. Use whenever the user asks to add a new db_/cw_/nr_ tool, a new query capability, or a new introspection/discovery tool to this repo.
---

# Add a new MCP tool

This server has three sources — `database`, `cloudwatch`, `newrelic` — each laid out identically
under `src/tools/<source>/`, `src/service/<source>_service.py`, `src/integrations/<source>/`. A new
tool always touches the same five layers, in this order. Never skip a layer or put logic in the
wrong one — see `src/../CLAUDE.md` for the full architecture rationale.

## 1. Decide what layer the new logic lives in

- **`integrations/<source>/queries.py`** — the raw SQL/NRQL/CloudWatch query text or a
  `build_x_sql(...)`/`build_x_query(...)` template function if the query is built from
  runtime-discovered values (table/column names, event types, etc).
- **`integrations/<source>/constants.py`** — any new regex, cap, blocklist, or magic literal the
  new tool needs.
- **`integrations/<source>/models.py`** — a `@dataclass` (inheriting `core.models.DictableMixin`)
  for the tool's response shape, if it returns anything more structured than a single scalar/list
  of scalars. One dataclass per distinct shape; nested shapes get their own dataclass too (see
  `database/models.py`'s `TableDescription` → `Column`/`ForeignKey`/`Index` for the pattern).
- **`integrations/<source>/guardrail.py`** — only if the new tool needs a new *validation rule*
  (not just reusing `validate_sql`/`validate_nrql`/`validate_log_groups` etc). Guardrail functions
  are pure — no I/O, no network/DB calls — and raise the source's `ToolError` subclass
  (`GuardrailError`/`CWGuardrailError`/`NewRelicGuardrailError` from `core/errors.py`) with a
  machine-readable `rule` + human `detail`.
- **`integrations/<source>/{client,engine,introspect}.py`** — actual I/O. Database catalog/data
  queries go in `introspect.py`; CloudWatch/New Relic API calls go in `client.py`.

## 2. Service method (`service/<source>_service.py`)

The service class (`DatabaseService`/`CloudWatchService`/`NewRelicService`, all extend
`service/base.py`'s `BaseService`) owns validate → execute → redact → respond:

```python
async def my_new_method(self, arg: str) -> dict:
    try:
        validated = validate_sql(...)              # or validate_nrql / validate_log_groups
        raw = await introspect.my_new_query(...)    # or client call
    except ToolError as e:
        return e.to_response()
    except SQLAlchemyError as e:                     # or (BotoCoreError, ClientError) / httpx.HTTPError
        return self._sqlalchemy_error_response(e)    # each service has its own error-response method
    result = MyModel(...)                             # build the dataclass
    return self.ok(result.to_dict(), {"row_count": len(...)})
```

- Redact PII/log content before returning: `core.redaction.redact_rows`/`redact_log_event`.
- Success envelope is always `self.ok(data, meta)` → `{"ok": True, "data": ..., "meta": ...}`.
  Never hand-roll this dict — every method in the file must use `self.ok(...)`.
- If the new function's return value is faked by an existing test or is a constructor-injected
  seam's contract (like `run_nrql_fn`, `logs_client_factory`), don't change that function's
  external return type — build a dataclass internally for typed construction, then flatten it
  back with `.to_dict()` before it crosses that boundary. See `extract-source-files` skill for the
  full DI-seam rule.

## 3. Tool file (`tools/<source>/<tool_name>.py`)

One file per tool, named after the tool. This file is *only* the `@mcp.tool()` decorator, the
docstring the client model reads (this is a real accuracy lever — be specific about what the tool
does, when to call it, and what params mean), and a one-line body:

```python
from core.app import mcp
from core.container import get_container


@mcp.tool()
async def db_my_new_tool(arg: str) -> dict:
    """One paragraph: what this does, when a client model should call it,
    and any non-obvious parameter semantics."""
    return await get_container().database_service.my_new_method(arg)
```

No logic here. If you're tempted to add any, it belongs on the service method instead.

## 4. Register the tool

Add the new tool function to `src/tools/<source>/__init__.py`'s import list and `__all__` — it
will never register with FastMCP otherwise. Nothing else needs touching; `src/tools/__init__.py`
already imports the three source packages, and `server.py` imports that.

## 5. Cookbook entry (if it's a query-writing tool)

If the new tool lets the client model write ad-hoc queries (like `db_run_query`/`nr_run_nrql_query`)
against a schema/field-naming pattern that's non-obvious, add an example to
`src/data/query_cookbook.yaml` (exposed as the `docs://query-cookbook` resource). Per CLAUDE.md
§4.4 this measurably improves query-generation accuracy — don't skip it for a tool where it'd help.

## 6. Tests

- **Unit tests** (`tests/unit/`): construct the service directly with an injected fake
  (`CloudWatchService(get_settings(), logs_client_factory=lambda s: fake_client)`,
  `NewRelicService(get_settings(), run_nrql_fn=fake_run_nrql)`) rather than monkeypatching. Database
  has no such seam — its only shape-level tests are integration tests.
- **Integration tests** (`tests/integration/`, `-m integration`): for anything hitting a real
  Postgres; auto-skips without a reachable DB.
- Run `uv run pytest tests/unit -q` (must stay 100% green, no external deps) and
  `uv run pytest tests/integration -m integration` if you touched `database`.

## 7. Verify

```powershell
uv run ruff check .
uv run pytest tests/unit -q
uv run pytest tests/integration -m integration   # if database was touched
```

Manually spot-check the new tool's JSON response once against a live source if practical —
`asdict()`-based dataclass serialization can shift key presence/ordering in ways unit tests won't
always catch.
