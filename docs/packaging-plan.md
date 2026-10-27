# Support Investigation MCP: Packaging and Distribution Plan

Date: 2026-10-27

## 1. Goal and current state

The Support Ticket Investigation skill on Agensi is only instructions, so nobody can use it until they also install, configure and start the MCP server it depends on. This plan makes that setup a one-block config for end users.

What already exists in the repo:

- A read-only FastMCP server with Postgres, CloudWatch and New Relic tools, served over streamable HTTP on `/mcp`.
- A `Dockerfile` and a GitHub Action that publishes the image to GHCR on every merge to `main`.
- A console script, `investigation-server`, and a hatchling build in `pyproject.toml`, so the project is close to a pip-installable package.

What blocks users today:

- The server only speaks HTTP, so the user must run a process and manage a port.
- The HTTP transport has no authentication, so it must stay bound to localhost.
- The package name `mcp-tool` is generic and `requires-python` is `>=3.13`.

## 2. Distribution options

Publishing to PyPI and launching over stdio with `uvx` is the lowest-effort path for individuals. Docker stays for teams that want a shared server.

| Option | User effort | Credentials stay on user's machine | Best for |
| --- | --- | --- | --- |
| `uvx` from PyPI (stdio) | One JSON block in the client config | Yes | Individuals |
| Docker image (HTTP) | `docker run` plus a `.env` file | Yes, if run locally | Teams, Linux or server hosts |
| Hosted shared instance | None for users, auth work for you | No | Org-wide use, needs auth in front of `/mcp` |

Keep both the `uvx` and Docker paths. They share the same code and differ only in the transport setting.

## 3. Code changes needed

1. **Add a stdio transport switch** in `src/server.py`. `main()` currently always calls `mcp.run(transport="streamable-http", ...)`. Select the transport with an env var (for example `MCP_TRANSPORT=stdio`) or a `--stdio` flag. Keep HTTP as the default for Docker and hosted use.
2. **Rename the package** in `pyproject.toml` from `mcp-tool` to a distinctive PyPI name, for example `opsinsight-mcp`. Set the version for the first release.
3. **Add a script named after the package** in `[project.scripts]` (`opsinsight-mcp = "server:main_stdio"`, keeping `investigation-server`). `uvx <package>` runs the command with the same name as the package, so without it `uvx opsinsight-mcp` fails.
4. **Lower the Python floor** if nothing needs 3.13. `uvx` fetches a suitable Python itself, but plain `pip` does not.
5. **Logging needs no change.** Verified: `core/logging_config.py` already sends logs to `sys.stderr`, and no `print()` or `sys.stdout` use exists under `src/`. Re-check after the stdio change, since stdout must carry only protocol messages.

## 4. User setup

### Option A: `uvx` (after publishing to PyPI)

Add this to the client's MCP config (Claude Desktop, Cursor):

```json
{
  "mcpServers": {
    "investigation": {
      "command": "uvx",
      "args": ["opsinsight-mcp"],
      "env": {
        "DB_URL": "postgresql+asyncpg://readonly:pass@host:5432/db",
        "CLOUDWATCH_REGION": "ap-south-1",
        "CLOUDWATCH_ACCESS_KEY_ID": "...",
        "CLOUDWATCH_SECRET_ACCESS_KEY": "...",
        "NEW_RELIC_API_KEY": "NRAK-...",
        "NEW_RELIC_ACCOUNT_ID": "..."
      }
    }
  }
}
```

For Claude Code, pass each variable with `-e` before the server name: `claude mcp add -e DB_URL=... -e CLOUDWATCH_REGION=... investigation -- uvx opsinsight-mcp`.

In stdio mode the app is launched by the client, so there is no `.env` file to read; the `env` block above is the configuration. The `opsinsight-mcp` command defaults to stdio, so no transport setting is needed.

The CloudWatch and New Relic values are optional.

### Option B: Docker

Create a `.env` file:

```env
DB_URL=postgresql+asyncpg://readonly_user:pass@host.docker.internal:5432/dbname
CLOUDWATCH_REGION=ap-south-1
CLOUDWATCH_ACCESS_KEY_ID=...
CLOUDWATCH_SECRET_ACCESS_KEY=...
CLOUDWATCH_ALLOWED_LOG_GROUP=/app/prod
NEW_RELIC_API_KEY=NRAK-...
NEW_RELIC_ACCOUNT_ID=...
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
```

Run it, replacing `<owner>/<repo>` with the GitHub path:

```bash
docker run -d --name investigation-mcp \
  -p 127.0.0.1:8000:8000 --env-file .env \
  ghcr.io/<owner>/<repo>:latest
```

Connect the agent:

- Claude Code: `claude mcp add --transport http investigation http://localhost:8000/mcp`
- Cursor or Claude Desktop: add `{"mcpServers":{"investigation":{"url":"http://localhost:8000/mcp"}}}`.

Gotchas inside a container:

- `SERVER_HOST=0.0.0.0` is required, or the port mapping cannot reach the server.
- `SERVER_PORT=8000` must be set explicitly. `.env.example` says 8001 but the Dockerfile exposes 8000.
- Use `host.docker.internal` instead of `localhost` for a database on the user's machine.
- `AWS_PROFILE` does not work because the container has no `~/.aws`. Use `CLOUDWATCH_ACCESS_KEY_ID` and `CLOUDWATCH_SECRET_ACCESS_KEY`.
- Publish the port on `127.0.0.1` only. The HTTP transport has no authentication.

## 5. How the skill solves a ticket

Example ticket: "Order 99283 failed at checkout around 2pm."

1. **Extract** the identifiers (order 99283), the time window, and the store or user from the ticket.
2. **Resolve** ambiguous names into IDs with `db_resolve_store`.
3. **Discover before querying.** Call `db_list_tables` then `db_describe_table`, `cw_list_log_groups` then `cw_describe_log_fields`, and `nr_list_event_types` then `nr_describe_log_fields`. Read the `docs://query-cookbook` resource. This avoids guessed field names that silently return zero rows.
4. **Query each source** for the same ID and time window: database rows and statuses, a CloudWatch Logs Insights query, and an NRQL query for errors and latency.
5. **Fix errors.** The server returns structured errors (scan limit exceeded, blocked syntax). Narrow the window or rewrite the query and retry.
6. **Merge** findings by timestamp and shared ID (request ID or trace ID). Compare the error rate to tell a one-off failure from a spike.
7. **Report** sources, evidence, root-cause analysis and next steps.

Caveat: cross-source correlation is manual. The composite `trace_request` tool does not exist, so the skill's `SKILL.md` must tell the agent to do the join itself.

Smoke test: ask the agent "Investigate why order 99283 failed." It should call `db_list_tables` first and return a report with evidence from each configured source.

## 6. Publish steps and open decisions

Steps:

1. Add the stdio switch to `src/server.py` (logging already goes to stderr).
2. Rename the package and set the version in `pyproject.toml`.
3. Test locally with `uvx --from . investigation-server` in stdio mode. This only works inside the project folder, since `.` is the current directory. Other computers use `uvx opsinsight-mcp` (after PyPI publish) or `uvx --from git+https://github.com/<owner>/<repo> investigation-server` (before publish). `uvx` downloads and caches the package automatically; users only need `uv` installed.
4. Run `uv build`, then `uv publish` (or add a GitHub Action that publishes on a version tag such as `v1.0.0`).
5. Add a "Quick start" section to `README.md` with the `uvx` and Docker blocks above.
6. Update the Agensi skill's setup text and `SKILL.md` with the same steps, plus a fallback line: "if the server is unreachable, start it and retry".

Status (2026-10-27): steps 1–3 are done.

- `MCP_TRANSPORT` (`stdio` | `streamable-http`) overrides either command's default. `investigation-server` defaults to HTTP; `opsinsight-mcp` defaults to stdio.
- Verified: `uvx --from . opsinsight-mcp` lists all 16 tools and 3 resources over stdio on Python 3.13 and 3.11; 109 unit tests pass on both; the wheel includes `query_cookbook.yaml`.
- Fixed: a fresh install failed with `No module named 'greenlet'`. The dependency is now `sqlalchemy[asyncio]`.

Decided:

- [x] PyPI package name: `opsinsight-mcp`.
- [x] Python floor: `>=3.11`.

- [x] Publishing: tag-triggered GitHub Action with PyPI trusted publishing (`.github/workflows/publish.yml`). Pushing `vX.Y.Z` lints, tests, checks the tag matches `pyproject.toml`'s version, builds, and publishes. Requires a one-time trusted-publisher registration on PyPI (owner `Vignesh-2552`, repo `mcp-log-db-investigator`, workflow `publish.yml`, environment `pypi`).

Open decisions:

- [ ] The wheel installs generic top-level modules (`core`, `tools`, `service`, `data`, ...). Fine under `uvx` (isolated env) but can clash in a shared `pip` env. Moving them under an `opsinsight/` package fixes it; it touches every import.
- [ ] Add a `docker-compose.yml` for the Docker path.
- [ ] Whether to offer a hosted shared instance, which needs authentication in front of `/mcp`.
