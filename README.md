<div align="center">

<img src="docs/assets/logo-512.png" alt="opsinsight-mcp logo" width="120">

# Investigation MCP Server

[![CI](https://github.com/Vignesh-2552/mcp-log-db-investigator/actions/workflows/ci.yml/badge.svg)](https://github.com/Vignesh-2552/mcp-log-db-investigator/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![Read-only](https://img.shields.io/badge/access-read--only-brightgreen)
![MCP](https://img.shields.io/badge/protocol-MCP-orange)

<h3>A <a href="https://gofastmcp.com">FastMCP</a> server exposing safe, read-only tools for investigating support tickets across PostgreSQL, AWS CloudWatch, and New Relic.</h3>

<div class="toc">
  <a href="#overview">Overview</a> •
  <a href="#architecture">Architecture</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#configure-your-ai-assistant">Configure Your AI Assistant</a> •
  <a href="#usage-examples">Usage Examples</a> •
  <a href="#mcp-server-api">MCP Server API</a> •
  <a href="#security-model">Security Model</a> •
  <a href="#development">Development</a>
</div>

</div>

## Overview

When a support ticket comes in ("checkout failed for user 4417 around 14:30"),
answering it usually means opening a DB client, the AWS Console, and a logging
platform, hand-writing a query in each, then manually cross-referencing
timestamps and ids across all three. This server turns that into a
conversation with an MCP client.

Features include:

- 🐘 **PostgreSQL** — schema discovery, validated read-only `SELECT`
  execution, PII-masked sample rows, and catalog-driven identifier/store
  lookup (no hardcoded table list).
- ☁️ **AWS CloudWatch** — log group discovery, Logs Insights queries
  (cost-capped by bytes scanned), pattern-based log filtering, and metric
  statistics.
- 🟢 **New Relic** — event type and log field discovery (`keyset()`),
  correlation-id detection, and validated NRQL execution.
- 🛡️ **Guardrails, not trust** — every query is AST-parsed (SQL) or
  pattern-validated (NRQL/CloudWatch) before it touches the network; results
  are PII-redacted before they're returned.

The server does **not** generate queries itself. The client model (Claude
Desktop, Cursor) writes the query, using schema/field-discovery tools and a
curated [query cookbook](src/opsinsight/data/query_cookbook.yaml) this server exposes as
grounding context — the server's job is to make sure the model has enough
context to write a *correct* query, and to validate + execute whatever it
writes safely. See [`docs/design.md`](docs/design.md) (§3.1) for the full
rationale.

Cross-source correlation — matching a `request_id`/`order_id` across the DB
and both log sources — is currently done by the client model itself; there's
no composite "investigate this ticket end-to-end" tool yet (see
[`docs/design.md`](docs/design.md) §4.5 for what's not implemented).

## Architecture

![Architecture: MCP client to Investigation MCP Server, fanning out to PostgreSQL, AWS CloudWatch, and New Relic](docs/assets/architecture.svg)

## Quick Start

### Fastest: install and run with `uvx`

You don't need to clone the repo, run Docker or open a port. Your MCP client
starts the server itself over stdio with `uvx`. `uvx` downloads the package
into an isolated environment the first time and reuses that copy afterwards.

#### Step 1: Install `uv`

`uvx` comes with [`uv`](https://docs.astral.sh/uv/getting-started/installation/).

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Open a **new** terminal, then check it:

```powershell
uv --version
uvx --version
```

`uv` downloads a suitable Python (3.11+) if you don't have one.

#### Step 2: Choose a version

| Source | `args` value | When to use |
|---|---|---|
| PyPI release | `["opsinsight-mcp==0.1.1"]` | A stable, pinned version |
| GitHub `main` | `["--from", "git+https://github.com/Vignesh-2552/mcp-log-db-investigator", "opsinsight-mcp"]` | The newest merged code |
| A branch or tag | `["--from", "git+https://github.com/Vignesh-2552/mcp-log-db-investigator@<branch-or-tag>", "opsinsight-mcp"]` | Testing unreleased work |

Changes listed under **Unreleased** in [`CHANGELOG.md`](CHANGELOG.md) aren't in
the PyPI release yet. That includes the audit log, the load limits and the
stricter SQL guardrail, so use the GitHub form if you need them.

To check that the package installs (this downloads it and exits, without starting the server):

```powershell
uvx --from opsinsight-mcp==0.1.1 python -c "import importlib.metadata as m; print(m.version('opsinsight-mcp'))"
```

#### Step 3: Get credentials for the sources you use

You only need the sources you plan to query. See
[What each source needs](#what-each-source-needs). Use a **read-only** DB role or
replica, an IAM user limited to your allowlisted log groups, and a New Relic
**User** key (`NRAK-...`).

#### Step 4: Add the server to your MCP client

In this mode there's no `.env` file; the `env` block below is the whole configuration.
Leave out the variables for sources you don't use.

**Claude Desktop**: edit `claude_desktop_config.json`
(Windows: `%APPDATA%\Claude\claude_desktop_config.json`, macOS:
`~/Library/Application Support/Claude/claude_desktop_config.json`).
**Cursor**: edit `.cursor/mcp.json` in your project, or `~/.cursor/mcp.json` for all projects.

```json
{
  "mcpServers": {
    "investigation": {
      "command": "uvx",
      "args": ["opsinsight-mcp==0.1.1"],
      "env": {
        "DB_URL": "postgresql+asyncpg://readonly_user:pass@host:5432/dbname",
        "CLOUDWATCH_REGION": "ap-south-1",
        "CLOUDWATCH_ACCESS_KEY_ID": "...",
        "CLOUDWATCH_SECRET_ACCESS_KEY": "...",
        "CLOUDWATCH_ALLOWED_LOG_GROUP": "/app/prod",
        "NEW_RELIC_API_KEY": "NRAK-...",
        "NEW_RELIC_ACCOUNT_ID": "..."
      }
    }
  }
}
```

**Claude Code**: one command. Add one `-e` for each variable you need, and use
`--scope user` to make it available in every project:

```powershell
claude mcp add investigation --scope user -e DB_URL=postgresql+asyncpg://readonly_user:pass@host:5432/dbname -- uvx opsinsight-mcp==0.1.1
```

Any tuning variable from `.env.example` can also go in `env`, for example
`DB_MAX_ROWS`, `RATE_LIMIT_RPS` or `PII_REDACTION`.

#### Step 5: Restart the client and test it

1. Fully quit and reopen Claude Desktop or Cursor; closing the window isn't enough.
2. Check that the `investigation` server shows **16 tools** (`db_*`, `cw_*`, `nr_*`).
3. Ask a simple question, such as *"List the database tables"* (which calls
   `db_list_tables`) or *"Which CloudWatch log groups can you read?"*.

The first launch takes a little longer while `uvx` downloads the package.

#### Updating

- **Pinned version:** change the version in `args`, e.g. `opsinsight-mcp==0.1.2`, then restart the client.
- **GitHub form:** add `"--refresh"` before `"--from"` once to fetch the newest commit, then remove it again.

#### Optional: run as an HTTP server with `uvx`

To run one shared HTTP server instead of a stdio process per client, use the
`investigation-server` command. Run it from a folder that contains your `.env`,
because settings load from `.env` in the current directory:

```powershell
uvx --from opsinsight-mcp==0.1.1 investigation-server
```

The same HTTP rules apply as when [running from source](#installation): it serves
`http://127.0.0.1:8001/mcp` by default and needs `MCP_AUTH_TOKEN` on any
non-loopback host.

#### Troubleshooting

| Symptom | Fix |
|---|---|
| `spawn uvx ENOENT` / server fails to start | The client can't find `uvx` on its PATH. Run `where uvx` (Windows) or `which uvx` (macOS/Linux) and put that full path in `"command"`. |
| Server starts but a source's tools fail | That source's variables are missing or wrong in `env`. The error message names the missing setting. |
| `server_busy` or "rate limit exceeded" errors | The load limits were hit. Wait a few seconds, or raise `DB_MAX_CONCURRENT_QUERIES` / `RATE_LIMIT_RPS` in `env`. |
| Need the server logs | Logs go to stderr, which the client captures. In Claude Desktop, look in the `logs` folder next to `claude_desktop_config.json` (`mcp-server-investigation.log`). |

**For developers:** to run your local checkout through `uvx`, use
`uvx --no-cache --from <path-to-repo> opsinsight-mcp`. Without `--no-cache`, uvx
can reuse an earlier build of the folder and miss your latest edits. For
day-to-day development, use `uv run` from the repo instead
([Installation](#installation)).

### Prerequisites (running from source)

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- Access credentials for whichever source(s) you want to use — see
  [What each source needs](#what-each-source-needs) below. You don't need
  all three; a source you skip just errors at call time instead of blocking
  startup.

### Installation

```powershell
uv sync
copy .env.example .env
# edit .env — fill in DB_URL and/or the CloudWatch/New Relic blocks you need
uv run investigation-server
```

The server starts on `http://127.0.0.1:8001/mcp` by default (streamable HTTP
transport; change it with `SERVER_PORT`). On loopback, a bearer token is
optional; set `MCP_AUTH_TOKEN` to require one. The server **refuses to start**
on a non-loopback `SERVER_HOST` unless `MCP_AUTH_TOKEN` is set (or you
explicitly set `ALLOW_UNAUTHENTICATED_HTTP=true` because an authenticating
proxy already guards it). Generate a token with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Requests are also checked against a Host/Origin allowlist (DNS-rebinding
protection): `localhost`, `127.0.0.1` and `::1` always work; add a public
hostname with `SERVER_ALLOWED_HOSTS`.

### Docker

For a long-running HTTP server (shared by a team, or on a server host), run the
image published to GHCR on every merge to `main`:

```powershell
docker run --rm -p 127.0.0.1:8000:8000 --env-file .env -e SERVER_HOST=0.0.0.0 -e SERVER_PORT=8000 -e MCP_AUTH_TOKEN=<your-token> ghcr.io/vignesh-2552/mcp-log-db-investigator:latest
```

Then point the client at `http://localhost:8000/mcp` with an
`Authorization: Bearer <your-token>` header (see
[Configure Your AI Assistant](#configure-your-ai-assistant)). To build the image
yourself instead: `docker build -t investigation-mcp .` and use
`investigation-mcp` as the image name.

What differs from the local (`uv run`) path:

- The container binds `0.0.0.0`, so it **will not start without
  `MCP_AUTH_TOKEN`** (put it in the `.env` or pass `-e`). It runs as an
  unprivileged user (uid 10001).
- If clients reach it by a hostname other than `localhost`, add that name to
  `SERVER_ALLOWED_HOSTS` (otherwise requests get HTTP 421).
- The image defaults to `SERVER_HOST=0.0.0.0` and `SERVER_PORT=8000`, so it is
  reachable through `-p ...:8000`. A `--env-file` value overrides those
  defaults, and `.env.example` sets `127.0.0.1` and `8001`; the `-e` flags
  above override the `.env` file again, so keep them (or drop `SERVER_HOST`
  and `SERVER_PORT` from the `.env` you use with Docker).
- Use `host.docker.internal` instead of `localhost` in `DB_URL` for a database
  running on your own machine.
- `AWS_PROFILE` won't work (there's no local AWS config file inside the
  image) — use `CLOUDWATCH_ACCESS_KEY_ID`/`CLOUDWATCH_SECRET_ACCESS_KEY`
  in `.env` instead if you need CloudWatch tools.

### What each source needs

| Source | Required | Notes |
|---|---|---|
| **PostgreSQL** (`db_*` tools) | `DB_URL` | Full connection string, `asyncpg` driver: `postgresql+asyncpg://user:pass@host:5432/dbname`. Point this at a read-only role/replica — see [Security Model](#security-model). |
| **AWS CloudWatch** (`cw_*` tools) | `CLOUDWATCH_REGION` + one of (`AWS_PROFILE`) or (`CLOUDWATCH_ACCESS_KEY_ID` + `CLOUDWATCH_SECRET_ACCESS_KEY`) + `CLOUDWATCH_ALLOWED_LOG_GROUP` | `CLOUDWATCH_REGION` has no `AWS_REGION` fallback — it must be set explicitly. `CLOUDWATCH_ALLOWED_LOG_GROUP` is a comma-separated allowlist; leaving it unset means no log group is queryable. |
| **New Relic** (`nr_*` tools) | `NEW_RELIC_API_KEY` + `NEW_RELIC_ACCOUNT_ID` | The API key must be a **User API key** (`NRAK-...`), not an ingest/license key. `NEW_RELIC_REGION` defaults to `us`; set it to `eu` for EU-region accounts. |

See `.env.example` for every available variable, including tuning knobs
(row limits, time-window caps, timeouts) that all ship with working defaults.

## Configure Your AI Assistant

**Cursor** — `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "investigation": {
      "type": "http",
      "url": "http://localhost:8001/mcp",
      "headers": { "Authorization": "Bearer <your MCP_AUTH_TOKEN>" }
    }
  }
}
```

Drop `headers` only for a loopback server with no `MCP_AUTH_TOKEN`.
**Claude Desktop** — same shape in `claude_desktop_config.json`.

## Usage Examples

### Investigate a failed order
Ask:
> Why did checkout fail for order 88213 around 14:30 IST yesterday?

### Find a user's records across the database
Ask:
> Find every row across the schema that references user_id 4417.

### Trace a request through the logs
Ask:
> Pull the log lines for order_id 88213 from /aws/ecs/checkout-svc for the last hour.

### Cross-check against New Relic
Ask:
> What event types have data in New Relic in the last hour, and what fields does the Log event type have?

## MCP Server API

For a junior-friendly reference with inputs, output structures, examples, and
tool update guidance, see [`docs/tool-reference.md`](docs/tool-reference.md).

| Tool Name | Description |
|---|---|
| `db_list_tables` | Tables + row estimates + comments (cached ~10 min). |
| `db_describe_table` | Columns, types, nullability, PK/FK, indexes. |
| `db_sample_rows` | Sample rows, PII-masked. |
| `db_explain_query` | `EXPLAIN (FORMAT JSON)` on a validated query. |
| `db_run_query` | Validated, read-only `SELECT` execution. |
| `db_search_by_identifier` | Find rows across catalog-discovered tables by order/user/payment/request id. |
| `db_resolve_store` | Resolve a store name/domain to a `store_id`, or return candidates if ambiguous. |
| `cw_list_log_groups` | Log groups + retention + stored bytes. |
| `cw_describe_log_fields` | Sample recent events, discover JSON fields + frequency. |
| `cw_run_insights_query` | `StartQuery` → poll → `GetQueryResults`, cost-capped. |
| `cw_get_trace_events` | Convenience wrapper: matching log lines for a field/value, sorted chronologically. |
| `cw_filter_events` | Simple pattern grep over recent events. |
| `cw_get_metric_stats` | CloudWatch metric datapoints. |
| `nr_list_event_types` | `SHOW EVENT TYPES` — enumerates event types with data (`Log`, `Transaction`, `Span`, `Metric`, custom types); run before `nr_describe_log_fields` if the event type is unknown. |
| `nr_describe_log_fields` | `keyset()` of a New Relic event type (default `Log`), flags likely trace/correlation id attributes; run before writing NRQL. |
| `nr_run_nrql_query` | Validated, read-only NRQL execution against New Relic (Log/Metric/event data). |

Resources: `schema://db/tables`, `schema://db/table/{name}`, `logs://groups`,
`docs://query-cookbook`.

## Security Model

Every `db_*`/`cw_*`/`nr_*` call goes through a guardrail layer *before* any
network I/O: SQL is parsed with `sqlglot` and rejected unless it's a single
`SELECT`/`WITH...SELECT` with no dangerous functions; CloudWatch calls are
checked against a log-group allowlist, a time-window cap, and a bytes-scanned
cost ceiling; NRQL is restricted to a single `SELECT` with no write-ish
keywords and a clamped `LIMIT`. Results and any log messages are redacted
(PII and credentials) before returning. Over HTTP, a bearer token
(`MCP_AUTH_TOKEN`) and strict Host/Origin checks guard the endpoint, and every
tool call and resource read is written as a structured audit event to the
`investigation_server.audit` logger (stderr). The latest audit is in
[`docs/security-audit.md`](docs/security-audit.md). See [`docs/design.md`](docs/design.md) §6 for the full model.

## Development

```powershell
uv run ruff check .                              # lint
uv run pytest tests/unit -q                      # guardrail/redaction tests, no external deps
uv run pytest tests/integration -m integration   # requires a reachable Postgres; auto-skips otherwise
```

- `tests/unit/` — pure logic, no network/DB dependency (the guardrail tests
  are the security-boundary tests and the highest priority in this suite).
- `tests/integration/test_db_tools.py` — runs against your configured
  Postgres; skipped automatically if it isn't reachable.

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs lint + both
test suites on every push/PR to `main`/`dev`.

See [`CLAUDE.md`](CLAUDE.md) for the architecture/layering conventions this
codebase follows, and [`docs/tool-reference.md`](docs/tool-reference.md) for
how to add a new tool. Contributing a change? See
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

[MIT](LICENSE)
