# Setup

This skill needs the Investigation MCP server, published on PyPI as
[`opsinsight-mcp`](https://pypi.org/project/opsinsight-mcp/) (open source, MIT:
https://github.com/Vignesh-2552/mcp-log-db-investigator). It runs on your own machine with your own
credentials; they never leave your environment.

## Requirements

- [`uv`](https://docs.astral.sh/uv/getting-started/installation/). `uvx` downloads the server and a suitable Python
  on first run, so no clone or Python install is needed.
- A **read-only** PostgreSQL role (the server also forces read-only sessions, but the role is the last line of defence)
- Optional: AWS credentials with CloudWatch Logs read access, and a New Relic User API key (`NRAK-...`)

## Connect your client (recommended: uvx)

Add the server to your MCP client's config, using `scripts/mcp-config.example.json` as the template:

- Claude Desktop: `claude_desktop_config.json`
- Cursor: `.cursor/mcp.json`
- Claude Code: `claude mcp add -e DB_URL=... investigation -- uvx opsinsight-mcp` (one `-e` per variable)

The client starts the server itself over stdio, so there is no port to open and nothing to keep running. Set only
the variables for the sources you use, in the config's `env` block:

| Source | Variables |
|---|---|
| PostgreSQL | `DB_URL` (`postgresql+asyncpg://user:pass@host:5432/dbname`) |
| CloudWatch | `CLOUDWATCH_REGION`, `CLOUDWATCH_ALLOWED_LOG_GROUP` (comma-separated allowlist), and `AWS_PROFILE` or `CLOUDWATCH_ACCESS_KEY_ID` + `CLOUDWATCH_SECRET_ACCESS_KEY` |
| New Relic | `NEW_RELIC_API_KEY`, `NEW_RELIC_ACCOUNT_ID`, and `NEW_RELIC_REGION=eu` for EU accounts |

Restart the client, then confirm the `db_*`, `cw_*` and `nr_*` tools are listed. A source you leave unconfigured only
fails when its tools are called; it does not stop the server from starting.

## Alternative: a shared HTTP server with Docker

For a team server, run the published image and point clients at its URL
(`scripts/mcp-config.http.example.json`):

```
docker run --rm -p 127.0.0.1:8000:8000 --env-file .env -e SERVER_HOST=0.0.0.0 -e SERVER_PORT=8000 ghcr.io/vignesh-2552/mcp-log-db-investigator:latest
```

Copy `scripts/env.example` to `.env` and fill in only the sources you use. Never commit `.env`. Inside the container,
use the access-key pair instead of `AWS_PROFILE`, and `host.docker.internal` instead of `localhost` in `DB_URL`.

The HTTP transport has no built-in authentication. Keep it bound to loopback or a private network, and put an
authenticated proxy in front of it before exposing it more widely.
