# Review checklist — OpsInsight FastMCP

What to look for, by area. `CLAUDE.md` is the source of truth for *how* each mechanism is meant to
work; this file lists the *failure modes* to hunt. Paths are under `src/opsinsight/`.

## MCP server and transport

- `server.py`: HTTP vs stdio entry points, host/port/path from `Settings`, middleware order
  (audit before rate limit, so rejected calls are still audited), clean shutdown of engines/clients.
- `core/transport_security.py`: bearer token compared in constant time; fail closed on a
  non-loopback `SERVER_HOST` without `MCP_AUTH_TOKEN`; Host/Origin checks strict; stdio path
  doesn't accidentally depend on HTTP-only middleware.
- `core/app.py`: single `mcp` instance; nothing imports `server.py` (circular import).

## MCP tools

For every file in `tools/<source>/`:

- Thin wrapper: decorator, docstring, typed parameters, one-line body. Logic in a tool file is a finding.
- Parameter schema: `Annotated`/`Field` constraints (bounds on limits, windows, `Literal` enums)
  so bad input fails at the schema, not deep in the service.
- Docstring tells the model when to call it, the grounding step to call first, and the envelope.
- Registered: imported in its package `__init__.py`, in `__all__`, in `EXPECTED_TOOLS`, documented
  in `docs/tool-reference.md`.
- Read-only: no tool can write, mutate external state, or execute unvalidated query text.
  Inventory tools (`db_list_tables`, `cw_list_log_groups`, `nr_list_event_types`) are the only
  guardrail bypasses and must take no user query text.

## Resources and prompts

- `resources/`: URIs fixed (no user-controlled path → no traversal), correct MIME type, the
  cookbook loads from package data, not CWD.
- `prompts/`: pure formatting (no I/O, settings or clock), user input wrapped by `quote_input`,
  every tool named actually registered (`prompts/common_test.py`), fetches audited with redacted args.

## Service layer

- Each method: guardrail → execute → redact → cap → `self.ok(...)`. Redact **before** capping.
- `ToolError` caught and `.to_response()`'d; the source's exception family mapped by its private
  `_*_error_response`; no bare `except Exception` that hides bugs or leaks raw messages.
- DB methods inside `self._query_slot()`.
- Clients constructor-injected via `service/ports.py` Protocols; no service importing another
  source's `integrations/*`.
- `TraceService`: no source-specific branches; per-source timeout; one source failing is reported
  in `data.sources`, not raised.

## Asyncio and resources

- Blocking I/O in `async def`: boto3 calls must run off the loop (`asyncio.to_thread` or
  equivalent); `time.sleep` in async code is a finding.
- Unbounded `asyncio.gather` / task creation (search fan-out must use `_gather_bounded`).
- Tasks created and never awaited or cancelled; timeouts that cancel without cleanup
  (e.g. a CloudWatch query left running — must be stopped).
- Shared mutable state across requests (module-level dicts, caches without locks or per-config keys).
- Engines/clients keyed per configuration and disposed in `reset_*`; `httpx` clients closed.

## Database (Postgres)

- `integrations/database/guardrail.py`: single statement, allowed root types only, blocked node
  types, denylist by name and prefix, `BLOCKED_TABLES`, LIMIT inject/clamp, sensitive-column
  rename/whole-row rejection. Try to construct a bypass (CTE, subquery, function alias, quoting,
  schema-qualified names, `LATERAL`, set operations) and check a test covers it.
- `engine.py`: `default_transaction_read_only`, statement and idle-in-transaction timeouts sent as
  asyncpg `server_settings` (not a connect-listener `SET`); pool size and pre-ping.
- `introspect.py` / `queries.py`: identifiers via `quote_ident`, parameters bound, catalog caches
  keyed per database.

## Redis / response cache

- Only successful, redacted, stable responses cached; never `run_query`, NRQL or an open window.
- `_cache_scope` includes everything that changes the response (DSN/account/region, allowlist,
  budget, `pii_redaction`).
- Fails open; retry-after respected; serialization round-trips; no secrets in keys.
- Redis is optional (`[redis]` extra): code paths must work when it isn't installed.

## External APIs (CloudWatch, New Relic)

- Timeouts on every call; retries only for idempotent reads with backoff; HTTP status and
  GraphQL `errors` both handled.
- CloudWatch: window clamp, log-group allowlist, `bytesScanned` checked every poll, stop on
  timeout, `nextToken` pagination capped.
- NRQL guardrail: literals/comments masked before regexes; every `LIMIT` clamped and every
  `SINCE`/`UNTIL` span capped, including subqueries; default window on the top-level query.
- Error details redacted before returning.

## Security (OWASP API Top 10 lens)

- Auth bypass, missing authorization on a tool, SQL/NRQL/Insights injection, SSRF (any
  user-controlled URL or host), command injection (`subprocess`, `os.system`), path traversal,
  unsafe deserialization (`pickle`, `yaml.load` without `SafeLoader`), hardcoded credentials
  (`git grep -nIiE "(password|secret|token|api_key)\s*=\s*['\"]"`), PII in logs or responses,
  log injection (newlines in logged user input), missing rate limits, excessive DB role rights.
- Redaction: `PII_COLUMN_NAMES` and regex patterns applied to rows, log text and error details;
  Luhn check on cards.
- Audit events contain tool name, outcome and redacted arguments — never tokens or raw secrets.

## Performance

- Response budgets (`DB_MAX_RESPONSE_BYTES`, `CW_MAX_RESPONSE_BYTES`, `NR_MAX_RESPONSE_BYTES`)
  enforced and `meta.truncated` set.
- Concurrency slots, queue timeouts and rate limits sized from `Settings`, not hardcoded.
- No N+1 catalog queries; TTL caches on hot metadata paths.

## Logging and observability

- Structured JSON events; a request/correlation id carried through audit and service logs where
  available; errors logged once with context, not swallowed.
- No secrets, tokens or unredacted PII in any log call (`git grep -n "logger\.\|logging\."` and
  inspect arguments).

## Config, Docker, CI

- `core/config.py`: every setting has a safe default or fails fast; secrets typed `SecretStr`
  where appropriate; `.env.example` lists every setting with a placeholder.
- `Dockerfile`: non-root user, pinned base image, no `.env` copied, tests excluded.
- Workflows: actions pinned by SHA, least-privilege `permissions`.

## Pre-commit

- Diff touches only approved findings; no debug prints, commented-out code or stray files.
- New/changed tools: registration, `EXPECTED_TOOLS`, docs, schema unchanged unless approved.
- No `.env`, credentials, or local paths staged (`git diff --cached --name-only`).
- `CHANGELOG.md` updated under Unreleased for user-visible changes.
