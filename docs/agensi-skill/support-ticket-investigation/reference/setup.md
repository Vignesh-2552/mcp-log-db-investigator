# Setup

This skill needs an Investigation MCP server that you run yourself. Your credentials never leave your environment.

## Get the server

The server is open source (MIT) and is not bundled in this skill:

```
git clone https://github.com/Vignesh-2552/mcp-log-db-investigator.git
cd mcp-log-db-investigator
```

## Requirements

- Python with `uv`
- A **read-only** PostgreSQL role (the server also forces read-only sessions, but the role is the last line of defence)
- Optional: AWS credentials with CloudWatch Logs read access, and a New Relic User API key (`NRAK-...`)

## Configure

Copy `scripts/env.example` to `.env` and fill in only the sources you use. Never commit `.env`.

## Run

```
uv sync
uv run investigation-server
```

The server listens on streamable HTTP at `http://127.0.0.1:8001/mcp` by default. It binds to loopback because the
HTTP transport is only as protected as the network around it. Do not expose it publicly without an authenticated
proxy or the shared-secret bearer token setting.

## Connect your client

Add the server to your MCP client using `scripts/mcp-config.example.json` as a template (Claude Desktop, Cursor,
Claude Code and similar). Restart the client, then confirm the `db_*`, `cw_*` and `nr_*` tools are listed.
