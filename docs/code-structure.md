# Code Structure

A file-by-file map of the repository and the rules for keeping it organized. For *why* the
design looks like this, see [`design.md`](design.md); for agent working rules, see
[`../AGENTS.md`](../AGENTS.md); for deeper architecture notes, see [`../CLAUDE.md`](../CLAUDE.md).

## 1. Top-level layout

```
mcp_tool/
├── AGENTS.md              # Rules for AI coding agents (start here)
├── CLAUDE.md              # Detailed architecture notes for Claude Code
├── CONTRIBUTING.md        # Human contributor guide, release steps
├── CHANGELOG.md           # Per-version change log
├── README.md              # Overview, quick start, client setup
├── Dockerfile             # Container image (HTTP transport)
├── pyproject.toml         # Package metadata, deps, pytest + coverage config
├── uv.lock                # Locked dependency versions
├── .env.example           # Every setting with a safe placeholder (copy to .env)
├── .github/workflows/
│   ├── ci.yml             # ruff + unit tests (coverage gate) + integration + docker build
│   ├── deploy.yml         # Push image to GHCR on merge to main
│   └── publish.yml        # Publish to PyPI on a v* tag
├── docs/
│   ├── design.md          # As-built system design
│   ├── tool-reference.md  # Every tool: params, response, examples
│   ├── code-structure.md  # This file
│   ├── security-audit.md  # Security review notes
│   ├── roadmap.md         # Planned work
│   ├── agensi-skill/      # Packaged skill for client-side investigation
│   └── assets/            # Logo and images
├── src/opsinsight/        # The package (see §2)
                           # (no tests/ folder: tests sit beside their module, see §4)
```

Local-only folders (`.venv`, `.uv-cache`, `.ruff_cache`, `.pytest_cache`, `dist`, `.coverage`,
`.env`) are build or tool output and should never be committed.

## 2. Package layout: `src/opsinsight/`

```
src/opsinsight/
├── server.py                    # Entry points: main() = HTTP, main_stdio() = stdio
├── core/                        # Shared, source-agnostic building blocks
│   ├── app.py                   # The single shared `mcp = FastMCP(...)` instance
│   ├── container.py             # Composition root: builds and caches services
│   ├── config.py                # `Settings` (pydantic-settings) + cached get_settings()
│   ├── errors.py                # ToolError + GuardrailError / CWGuardrailError / NewRelicGuardrailError
│   ├── redaction.py             # PII masking by column name and regex
│   ├── audit.py                 # AuditMiddleware: one JSON event per tool call / resource read
│   ├── rate_limit.py            # Global rate-limiting middleware
│   ├── transport_security.py    # Bearer auth + Host/Origin checks (HTTP only)
│   ├── time_window.py           # clamp_window / parse_iso8601 (shared by all time-bounded sources)
│   ├── limits.py                # Cell truncation + JSON byte budget for row lists
│   ├── cache.py                 # TTL cache helpers
│   ├── field_heuristics.py      # Detect likely trace / correlation-id fields
│   ├── logging_config.py        # Logging setup (stderr)
│   └── models.py                # Shared models
├── tools/                       # Thin @mcp.tool() wrappers — one file per tool
│   ├── database/                # db_list_tables, db_describe_table, db_sample_rows,
│   │                            # db_explain_query, db_run_query, db_search_by_identifier,
│   │                            # db_resolve_store
│   ├── cloudwatch/              # cw_list_log_groups, cw_describe_log_fields,
│   │                            # cw_run_insights_query, cw_get_trace_events,
│   │                            # cw_filter_events, cw_get_metric_stats
│   ├── newrelic/                # nr_list_event_types, nr_describe_log_fields, nr_run_nrql_query
│   └── trace/                   # trace_request (cross-source)
├── service/                     # Business logic — one class per source
│   ├── base.py                  # BaseService: settings handle + ok() envelope
│   ├── ports.py                 # Protocols for injected clients (LogsClient, MetricsClient,
│   │                            # NrqlRunner, EngineFactory, DatabaseCatalog)
│   ├── database_service.py      # DatabaseService (injectable engine + catalog, query slots)
│   ├── cloudwatch_logs_service.py    # CloudWatchLogsService (log groups, Insights, filter, trace)
│   ├── cloudwatch_metrics_service.py # CloudWatchMetricsService (metric statistics)
│   ├── cloudwatch_common.py     # aws_error_response, shared by both CloudWatch services
│   ├── newrelic_service.py      # NewRelicService (injectable run_nrql_fn)
│   ├── trace_service.py         # TraceService: runs registered TraceSources, merges timeline
│   ├── trace_sources.py         # TraceSource Protocol + one adapter per source
│   └── trace_models.py          # TraceRequest, TraceJob, SourceResult, timeline models
├── integrations/                # Per-source validation and I/O
│   ├── database/
│   │   ├── guardrail.py         # sqlglot AST validation, function denylist, LIMIT clamp (pure)
│   │   ├── engine.py            # Async engine per DSN, read-only session hardening
│   │   ├── introspect.py        # Catalog queries (tables, columns, identifier search)
│   │   ├── queries.py           # Fixed SQL text
│   │   ├── constants.py / models.py
│   ├── cloudwatch/
│   │   ├── guardrail.py         # Log-group allowlist, window clamp (pure)
│   │   ├── client.py            # boto3 Logs / Metrics clients, cached per region+creds
│   │   ├── queries.py           # Fixed Logs Insights query text
│   │   ├── constants.py / models.py
│   └── newrelic/
│       ├── guardrail.py         # Regex NRQL validation with literal masking (pure)
│       ├── client.py            # NerdGraph GraphQL client (httpx)
│       ├── queries.py           # Fixed NRQL text
│       ├── constants.py / models.py
├── resources/                   # @mcp.resource() modules
│   ├── cookbook.py              # docs://query-cookbook
│   ├── schema.py                # Schema resources
│   └── logs.py                  # Log resources
└── data/
    └── query_cookbook.yaml      # Curated example queries (an accuracy lever, not just docs)
```

## 3. Dependency rules

```
tools/<source>/<tool>.py
        │  get_container().<source>_service.<method>(...)
        ▼
service/<source>_service.py ── core/redaction, core/errors, BaseService.ok
        │
        ├──► integrations/<source>/guardrail.py   (pure: validate / reject / clamp)
        └──► integrations/<source>/client.py | engine.py | introspect.py   (I/O)
```

| Layer | May import | Must not import |
|---|---|---|
| `tools/*` | `core.app`, `core.container` | `integrations/*`, other tools |
| `service/*` | `core/*`, `service/ports.py`, `integrations/<own source>/*` | `tools/*`, another source's `integrations/*` |
| `service/trace_service.py` | `TraceSource` Protocol, `core/*` | any concrete source service or `integrations/*` |
| `service/trace_sources.py` | the source **services**, their own source's constants | any `integrations/*` client |
| `integrations/*/guardrail.py` | `core.errors`, constants, `sqlglot`/`re` | anything doing I/O |
| `integrations/*/client.py`, `engine.py` | `core.config`, SDKs | `service/*`, `tools/*` |
| `core/*` | other `core/*` | `tools/*` (except `container.py` → `service/*`) |

Exception: inventory methods (`list_tables`, `list_log_groups`, `list_event_types`) send a fixed,
parameter-free query straight to the client without the query guardrail, since there is no user
query text to validate.

## 4. Tests layout

Every test file sits **beside the module it tests**, named `<module>_test.py`. There is no
`tests/` folder. `pyproject.toml` sets `testpaths = ["src"]` and `python_files = ["*_test.py"]`;
test files and `conftest.py` are excluded from the wheel, the Docker image and coverage.

```
src/opsinsight/
├── conftest.py                       # settings_override fixture, Postgres auto-skip for integration tests
├── server.py / server_test.py
├── core/
│   ├── audit.py / audit_test.py      # audit_test.EXPECTED_TOOLS pins the tool inventory
│   ├── time_window.py / time_window_test.py
│   ├── limits.py / limits_test.py
│   ├── rate_limit.py / rate_limit_test.py
│   └── ...                           # cache, config, container, logging_config, redaction, transport_security
├── integrations/
│   ├── database/   guardrail_test.py, engine_test.py, introspect_test.py, queries_test.py
│   ├── cloudwatch/ guardrail_test.py
│   └── newrelic/   guardrail_test.py, client_test.py
├── service/
│   ├── database_service_test.py
│   ├── database_service_limits_test.py        # byte budget, query slots, fan-out
│   ├── database_service_integration_test.py   # real Postgres; marked `integration`
│   ├── cloudwatch_logs_service_test.py
│   ├── cloudwatch_logs_service_inventory_test.py
│   ├── cloudwatch_metrics_service_test.py
│   ├── newrelic_service_test.py
│   └── trace_service_test.py
├── resources/schema_test.py
└── tools/tool_wiring_test.py         # every tool calls the right service method
```

Naming rules:

- One module, one test file: `foo.py` → `foo_test.py`.
- A module needing more than one test file: `foo_<topic>_test.py`.
- Tests needing a real Postgres: `foo_integration_test.py`, marked `@pytest.mark.integration`.

Where a test goes:

| You changed | Add / update tests in |
|---|---|
| Any module `x.py` | `x_test.py` next to it |
| A guardrail | `integrations/<source>/guardrail_test.py` (allowed **and** rejected cases) |
| A tool file / registration | `tools/tool_wiring_test.py`, `core/audit_test.py` (`EXPECTED_TOOLS`) |
| DB behaviour needing real Postgres | `service/database_service_integration_test.py` |

## 5. Naming conventions

- **Tools:** `<source-prefix>_<verb>_<noun>` — `db_`, `cw_`, `nr_`; cross-source tools have no
  prefix (`trace_request`). File name = tool name.
- **Inventory / grounding pairs:** each source has a `*_list_*` and a `*_describe_*` tool.
- **Services:** `<Source>Service` in `service/<source>_service.py`; public method name matches the
  tool name without the prefix (`db_run_query` → `DatabaseService.run_query`).
- **Errors:** one `ToolError` subclass per source; `rule` is a short snake_case id
  (`unsupported_time_range`, `server_busy`).
- **Settings:** env var = upper-case field name, grouped by prefix (`DB_`, `CW_`, `NR_`,
  `TRACE_`, `SERVER_`, `RATE_LIMIT_`).
- **Branches:** `<type>/<short-kebab-description>`; **commits:** Conventional Commits.

## 6. Adding a whole new data source

1. `integrations/<source>/` with `guardrail.py` (pure), `client.py`, `queries.py`,
   `constants.py`, `models.py`, and a `<Source>GuardrailError` in `core/errors.py`.
2. `service/<source>_service.py` with a `<Source>Service(BaseService)`, an injectable client
   in its constructor typed by a Protocol in `service/ports.py`, and a private
   `_<lib>_error_response`.
3. Wire it in `core/container.py` (constructor kwarg + lazy cached property).
4. `tools/<source>/` package with at least a `*_list_*` and `*_describe_*` tool, its own
   `__init__.py`, and add the package to `tools/__init__.py`.
5. Settings in `core/config.py` + `.env.example`; tests; docs; cookbook entries.
6. Optionally write a `TraceSource` adapter in `service/trace_sources.py` and add it to
   `default_trace_sources()` so `trace_request` covers it (no change to `TraceService`).

## 7. Keeping this document current

Update docs in the same PR as the code change:

| Change | Update |
|---|---|
| New / removed / renamed file or folder | This file (§1–§4) |
| New tool | `docs/tool-reference.md`, `core/audit_test.py`, `README.md` tool list, `docs/design.md` §4 |
| New setting | `.env.example`, `docs/design.md` §7.2 |
| New convention or layering rule | `AGENTS.md`, `CLAUDE.md`, this file §3/§5 |
| Guardrail / security change | `docs/security-audit.md`, `docs/design.md` §6 |
| Feature from "not implemented" list | `docs/design.md` §4.5, `CLAUDE.md`, `AGENTS.md` |
| Release | `CHANGELOG.md`, `pyproject.toml` version, `uv.lock` |
