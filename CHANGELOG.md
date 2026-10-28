# Changelog

All notable changes to `opsinsight-mcp` are listed here. Versions follow
[Semantic Versioning](https://semver.org/). A version number can be published
to PyPI only once, so every release gets a new number.

## [Unreleased]

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

[Unreleased]: https://github.com/Vignesh-2552/mcp-log-db-investigator/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Vignesh-2552/mcp-log-db-investigator/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/Vignesh-2552/mcp-log-db-investigator/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Vignesh-2552/mcp-log-db-investigator/releases/tag/v0.1.0
