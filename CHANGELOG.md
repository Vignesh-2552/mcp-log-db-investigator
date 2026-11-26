# Changelog

All notable changes to `opsinsight-mcp` are listed here. Versions follow
[Semantic Versioning](https://semver.org/). A version number can be published
to PyPI only once, so every release gets a new number.

## [Unreleased]

### Fixed
- README images (logo, architecture diagram) and doc links now use absolute GitHub URLs, so
  they render on the PyPI project page instead of showing as broken images.

### Added
- **Linear / Jira ticket tools** (read-only): `ticket_get_issue`, `ticket_search_issues`,
  `ticket_list_comments`. Configure `LINEAR_API_KEY` and/or `JIRA_BASE_URL` + `JIRA_EMAIL` +
  `JIRA_API_TOKEN` (Jira Cloud). Ticket text is PII-redacted, people carry no email, Jira ADF is
  converted to plain text, responses are size-capped and marked
  `meta.content_trust: "untrusted_user_content"`, and nothing is cached. `/investigate_incident`
  now accepts `ticket_id` (+ `provider`) instead of pasted `ticket_text`; with neither, it asks
  the user first. `doctor`'s error sanitizer masks the Linear key, the Jira token and its
  Basic-auth form.
- **Response cache with optional Redis backend** (`CACHE_BACKEND=memory|redis|none`, default
  `memory`). Caches successful, redacted responses for table/column metadata, log groups,
  New Relic event types/attributes (`CACHE_METADATA_TTL_S`), and CloudWatch Insights results
  for windows that closed at least `CW_RESULT_CACHE_SETTLE_S` ago (`CW_RESULT_CACHE_TTL_S`),
  so repeating a past-window query costs no scan. Responses carry `meta.cache`. Redis
  (`REDIS_URL`, install `opsinsight-mcp[redis]`) shares the cache across replicas; it fails
  open with a back-off, keys are hashed, and `doctor` checks connectivity.

### Security
- **DB session hardening now survives pooled connections.** `default_transaction_read_only`,
  `statement_timeout` and `idle_in_transaction_session_timeout` were applied with `SET` from a
  pool "connect" listener; SQLAlchemy's asyncpg adapter ran it inside a transaction that was
  rolled back after the first checkout, so every later checkout had no statement timeout and
  read-write defaults. They are now asyncpg `server_settings`. Integration test added.
- Redaction bypasses closed: a sensitive column returned under another name
  (`password_hash AS x`, `upper(token)`, UNION, column-alias lists) is rejected
  (`sensitive_column_renamed`), as are whole-row values like `row_to_json(u)`
  (`whole_row_reference`). DB, AWS and New Relic error details are redacted.
- `pg_shadow`, `pg_authid`, `pg_stat_activity`, `pg_hba_file_rules` and similar catalogs are
  blocked (`blocked_table`); `pg_stat_reset*` and `pg_stat_statements_reset` are blocked functions.
- NRQL: every `LIMIT` and every subquery's `SINCE`/`UNTIL` window are now enforced, not just
  the first match; the default `SINCE` is injected on the outer query.
- CloudWatch Insights queries still running when polling ends are stopped instead of being
  left scanning with no ceiling, and the bytes-scanned ceiling is checked on every poll.
- GitHub Actions pinned to commit SHAs; the image is published only after CI passes on main.

### Fixed
- `bytea` and other non-JSON column types no longer crash DB tools (UTF-8 text or `\x` hex).
- Duplicate column names in a result (`SELECT o.id, u.id ...`) are kept as `id`, `id_2`
  instead of silently dropping one.
- `db_resolve_store` applies `DB_MAX_RESPONSE_BYTES` and treats `%`/`_` in the input literally.
- `cw_filter_events` / `cw_describe_log_fields` follow `nextToken`, so an empty first page no
  longer reads as "no events".
- `db_search_by_identifier` ranks entity-named tables (`order_payment` for `order_id`) ahead of
  unrelated ones under the 25-table cap.
- Card redaction is Luhn-checked (long numeric ids and epoch microseconds are no longer masked),
  keeps the following space, and also covers card numbers in integer columns.
- NRQL `FROM ... SELECT` queries are accepted; a blocked keyword used as an attribute name gets a
  hint to backtick it. SQL ending in `; -- comment` is accepted.
- Schema resources go through `DatabaseService` (concurrency slot, error envelope).
- Numeric settings are validated (e.g. `DB_MAX_CONCURRENT_QUERIES=0` is rejected at startup).
- `fastmcp` minimum raised to 4.1 (3.x lacks the HTTP Host/Origin options the server passes).

### Added
- `CW_MAX_RESPONSE_BYTES` and `NR_MAX_RESPONSE_BYTES` (default 1 MB), `meta.truncated` on
  CloudWatch and NRQL results.
- `PG_CHECK_TIMEOUT_S` sets the integration-test reachability timeout (default 10 s, was 3 s),
  so tests against a remote replica no longer skip.

### Changed
- `cw_run_insights_query` / `cw_get_trace_events` return `status: "Stopped"` instead of
  `"Running"` when a query outlives `CW_POLL_MAX_WAIT_S`.

## [0.3.1] - 2026-11-03

### Fixed
- README install examples still pinned `opsinsight-mcp==0.2.0`; they now pin 0.3.1,
  so the PyPI page shows the current version.

## [0.3.0] - 2026-11-03

### Added
- MCP prompts `investigate_incident`, `trace_user_journey` and `slow_endpoint_rca`
  (`src/opsinsight/prompts/`): guided investigation workflows that clients show as
  slash commands. They do no I/O, quote user input as data, and name only registered
  tools (enforced by a test). Prompt fetches are audited with redacted arguments.
- Query cookbook entries for New Relic `Transaction` latency (percentiles, DB vs
  external time, share over a threshold), slow datastore and external spans, and RDS
  saturation metrics. A test now checks every NRQL cookbook entry passes the guardrail.
- `opsinsight-mcp doctor` / `investigation-server doctor` command for pre-flight
  diagnostics without booting the server. Validates PostgreSQL connectivity &
  read-only status, AWS CloudWatch credentials & allowlisted log groups, New
  Relic NerdGraph authentication, and server transport/security configurations,
  with strict secret masking and deterministic exit codes (`0` on healthy/skipped,
  `1` on failure).
- CLI `--help` (`-h`) and `--version` (`-v`) options for the server entry points.

### Changed
Internal refactor; no tool names, parameters or response shapes changed.
- `trace_request` now runs pluggable `TraceSource` adapters (`service/trace_sources.py`)
  instead of branching on source names inside `TraceService`. Adding a source no
  longer means editing `TraceService`.
- `CloudWatchService` is split into `CloudWatchLogsService` and `CloudWatchMetricsService`.
- `DatabaseService` takes an injectable `engine_factory` and `catalog`, like the
  other services. Injected clients are typed by Protocols in `service/ports.py`.
- Shared helpers moved to `core/`: `clamp_window` → `core/time_window.py`,
  `fit_rows_to_budget` / `truncate_cell` / `truncate_row_cells` → `core/limits.py`.

- Tests moved from `tests/` to sit beside the module they test (`foo.py` →
  `foo_test.py`). Test files are excluded from the wheel, Docker image and coverage.
  Run them with `uv run pytest -m "not integration"`.

### Removed
- Unused `get_connection()` from `integrations/database/engine.py`.

### Docs
- Added `AGENTS.md` (rules for AI coding agents) and `docs/code-structure.md`.

## [0.2.0] - 2026-10-28

### Security
See `docs/security-audit.md` for the full audit (2026-10-28).
- **Breaking:** the HTTP transport now refuses to start on a non-loopback
  `SERVER_HOST` (including the Docker image, which binds `0.0.0.0`) unless
  `MCP_AUTH_TOKEN` is set, or `ALLOW_UNAUTHENTICATED_HTTP=true` is set explicitly.
  Clients send the token as `Authorization: Bearer <token>`.
- **Breaking:** Host/Origin (DNS-rebinding) validation is always strict. Hostnames
  other than `localhost`/`127.0.0.1`/`::1` must be listed in `SERVER_ALLOWED_HOSTS`.
  Before, a container's published port accepted rebinding requests from any web page.
- SQL guardrail: blocked `dblink*` (incl. `dblink_exec`, which writes over a
  separate non-read-only connection), `query_to_xml` and related functions that
  run a SQL string, `pg_sleep_for`/`pg_sleep_until`, advisory locks, `set_config`,
  `lo_*`, `pg_ls_*`, `pg_stat_file`, `pg_reload_conf`, `nextval`/`setval`,
  `SELECT ... INTO`, `FOR UPDATE/SHARE`, and quoted function names (`"pg_sleep"(...)`).
- Redaction masks credentials in text (`password=...`, bearer tokens, JWTs, AWS
  and New Relic keys, PEM private keys) and any column/field whose name contains a
  secret-like word (`api_key`, `client_secret`, `*_token`, ...), including
  CloudWatch Insights result fields.
- Every tool call and resource read emits a structured audit event (actor, tool,
  redacted arguments, outcome, duration) on the `investigation_server.audit` logger.
- `cw_run_insights_query` rejects the `SOURCE` command, so queries can only touch
  allowlisted log groups. `cw_list_log_groups` returns nothing when
  `CLOUDWATCH_ALLOWED_LOG_GROUP` is unset instead of listing every log group.
- Length caps on SQL (50k chars), NRQL (20k) and Logs Insights (10k) queries.
- The Docker image runs as an unprivileged user (uid 10001).
- `DB_URL` is a secret setting (kept out of reprs and validation errors).
- Load limits:
  - Request rate limit on every MCP request (`RATE_LIMIT_RPS`, default 5/s, burst 20;
    `0` disables it). Over-limit requests get a JSON-RPC "rate limit exceeded" error.
  - DB tool responses are capped at `DB_MAX_RESPONSE_BYTES` (default 1 MB). Extra rows are
    dropped and the response is flagged with `meta.truncated` / `meta.truncation_note`.
  - At most `DB_MAX_CONCURRENT_QUERIES` (default 4) DB tool calls run at once; others fail
    with `server_busy` after `DB_QUEUE_TIMEOUT_S`.
  - `db_search_by_identifier` / `db_resolve_store` probe at most 3 tables at a time instead
    of all 25 in parallel, so one call can't take the whole connection pool.
  - `db_sample_rows` now truncates oversized cells like `db_run_query` does.
- Lockfile: authlib 1.9.1, pyjwt 2.15.1, urllib3 2.8.0, dnspython 2.9.0,
  opentelemetry-api 1.45.1 (transitive; fixes the advisories pip-audit reported).

### Changed
- All code moved into the `opsinsight` package (`src/opsinsight/`). The wheel now
  installs one top-level package instead of generic modules (`core`, `tools`,
  `service`, `data`, ...) that could clash in a shared environment. The
  `opsinsight-mcp` and `investigation-server` commands are unchanged.
- Images moved to `docs/assets/`; the README shows the logo. Removed duplicate
  logo formats and the completed `docs/packaging-plan.md` (superseded by this
  changelog and `docs/roadmap.md`).
- `.python-version` set to 3.13 to match CI and the Docker image.
- `nr_run_nrql_query` now enforces `NR_MAX_WINDOW_HOURS` on the query's `SINCE`/`UNTIL`
  span and adds `SINCE <NR_DEFAULT_WINDOW_HOURS> hours ago` when the query has none.
  Unrecognised `SINCE` forms return `unsupported_time_range`.
- `db_run_query`/`db_explain_query` accept `UNION`, `INTERSECT` and `EXCEPT` of selects.
- The Docker image pins `uv` (0.11.16) instead of `latest`.

### Fixed
- PII redaction no longer masks Unix epoch timestamps in log text (10-digit seconds
  were redacted as phone numbers, 13-digit millis as card numbers).
- The NRQL guardrail no longer rejects queries whose string literals contain a
  blocked keyword or `;` (e.g. `WHERE message LIKE '%update failed%'`), handles
  `LIMIT MAX`, and no longer appends `LIMIT` inside a trailing `--` comment.
- Services built with an override `Settings` now really use it: the DB engine and
  CloudWatch clients are cached per configuration instead of one process-wide
  instance, and schema introspection uses the service's settings.
- `schema://db/tables` and `schema://db/table/{name}` return a structured error
  instead of raising when the database is unreachable.

## [0.1.1] - 2026-10-28

### Fixed
- A non-numeric `NEW_RELIC_ACCOUNT_ID` (for example the `CHANGE_ME` placeholder
  from the example config) crashed New Relic tools with a raw `ValueError`. They
  now return a structured `invalid_account_id` error that names the bad value.
- The Docker image now defaults to `SERVER_HOST=0.0.0.0` and `SERVER_PORT=8000`,
  matching its `EXPOSE`d port. Before, `docker run` without overrides listened on
  `127.0.0.1:8001` inside the container and was unreachable.

### Added
- PyPI project links (homepage, repository, issues, changelog).
- Dependabot updates for Python dependencies, GitHub Actions and the Docker base image.
- `docs/roadmap.md`: the plan from 0.1 to 1.0.

### Changed
- `docs/design.md` now describes the stdio transport, the `uvx opsinsight-mcp`
  setup, Python 3.11+ and the real default ports.

## [0.1.0] - 2026-10-28

First PyPI release.

### Added
- `opsinsight-mcp` command (stdio by default) for `uvx opsinsight-mcp`;
  `investigation-server` stays on streamable HTTP. `MCP_TRANSPORT` overrides either.
- Docker image published to GHCR on every merge to `main`.
- Tag-triggered PyPI publishing with trusted publishing.

### Fixed
- Fresh installs failed with `No module named 'greenlet'`; the dependency is now
  `sqlalchemy[asyncio]`.

### Changed
- Minimum Python lowered from 3.13 to 3.11.

[Unreleased]: https://github.com/Vignesh-2552/mcp-log-db-investigator/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/Vignesh-2552/mcp-log-db-investigator/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Vignesh-2552/mcp-log-db-investigator/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Vignesh-2552/mcp-log-db-investigator/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/Vignesh-2552/mcp-log-db-investigator/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Vignesh-2552/mcp-log-db-investigator/releases/tag/v0.1.0
