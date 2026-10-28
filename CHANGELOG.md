# Changelog

All notable changes to `opsinsight-mcp` are listed here. Versions follow
[Semantic Versioning](https://semver.org/). A version number can be published
to PyPI only once, so every release gets a new number.

## [Unreleased]

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

[0.1.1]: https://github.com/Vignesh-2552/mcp-log-db-investigator/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Vignesh-2552/mcp-log-db-investigator/releases/tag/v0.1.0
