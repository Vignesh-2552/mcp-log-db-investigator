# Deployment Guide

How to take this server from local dev to a running deployment on
[Prefect Horizon](https://horizon.prefect.io), and what to check before and
after. Written for whoever runs the deploy, not just the person who wrote
the code.

## Why this needs care before going public

The MCP streamable-HTTP transport (`core/app.py` → `mcp.run(transport="streamable-http", ...)`)
has no authentication of its own. Horizon puts its OAuth 2.1 gateway in front
of the process and rejects unauthenticated clients before requests reach this
server. Do not expose the application through a route that bypasses that
gateway.

## Pre-deploy checklist

- [ ] `uv run pytest tests/unit -q` passes locally.
- [ ] `uv run ruff check .` is clean.
- [ ] Horizon's OAuth 2.1 gateway is enabled, and the application cannot be
      reached through a route that bypasses it.
- [ ] `.env` is confirmed absent from git (`git ls-files | findstr .env` →
      only `.env.example` should show up). It's gitignored and has never been
      committed, but re-check after any merge or history rewrite.
- [ ] You have the real values ready for whichever integrations you use:
      `DB_URL`, `CLOUDWATCH_*`, `NEW_RELIC_*` (see table below) — these go
      into Horizon's environment-variable UI, never into a file in the repo.

## Environment variables

Set these in Horizon's dashboard (not in a committed file). Full defaults
and comments live in `.env.example` — this table is the quick-reference.

| Variable | Required? | Notes |
|---|---|---|
| `DB_URL` | Yes | `postgresql+asyncpg://user:pass@host:5432/db`. Contains a real password — treat as a secret. |
| `SERVER_HOST` | Yes for deployment | Defaults to `127.0.0.1` (unreachable from outside the container). Set to `0.0.0.0` on Horizon. |
| `SERVER_PORT` | No | Falls back to the platform's `$PORT` automatically (`core/config.py`) if you don't set it explicitly. Leave unset unless you have a reason to pin it. |
| `CLOUDWATCH_REGION` | Only if using `cw_*` tools | No `AWS_REGION` fallback — must be this exact name. |
| `AWS_PROFILE` **or** `CLOUDWATCH_ACCESS_KEY_ID`+`CLOUDWATCH_SECRET_ACCESS_KEY` | Only if using `cw_*` tools | Pick one auth method, not both. A boto3 profile won't exist on Horizon's containers — use the access-key pair there. |
| `CLOUDWATCH_ALLOWED_LOG_GROUP` | **Required if using `cw_*` tools** | Comma-separated allowlist; an empty allowlist rejects every requested log group. |
| `NEW_RELIC_API_KEY` | Only if using `nr_*` tools | A User API key (`NRAK-...`), not an ingest/license key. |
| `NEW_RELIC_ACCOUNT_ID` | Only if using `nr_*` tools | |
| `PII_REDACTION` | No | Defaults `true`. Only disable for local debugging, never in a deployment. |

## Deploying to Horizon

Horizon (built by the FastMCP team) builds and hosts straight from a git
repo — there's no Dockerfile in this repo by design.

1. Push this repo to GitHub/GitLab.
2. In the Horizon dashboard, connect the repository. It detects the
   Python/FastMCP project and builds/containerizes it automatically.
3. Set the environment variables from the table above in Horizon's
   dashboard, including `SERVER_HOST=0.0.0.0`.
4. Deploy. Horizon's own gateway sits in front with OAuth 2.1 auth
   (mandatory on the free tier, enforced before requests reach this code)
   and gives you a stable production URL ending in `/mcp`.

## After deploying: verify it actually works

Don't treat "the build succeeded" as "it's working" — check:

1. **The endpoint is reachable.** `curl -i https://<your-deployment>/mcp`
   should not time out or connection-refuse (an unauthenticated request may
   still get a 401/403 from the gateway — that's expected and fine).
2. **A real client can connect.** Point Cursor or Claude Desktop at the
   deployed URL (see the client config in the main [README](../README.md#registering-with-an-mcp-client))
   and run `db_list_tables` or `nr_list_event_types` — pick whichever source
   you configured — to confirm credentials actually work from Horizon's
   network, not just locally.
3. **The gateway rejects unauthenticated clients.** Send a request without
   Horizon OAuth credentials and confirm the gateway refuses it. Then connect
   with a real OAuth-authenticated client and confirm the request reaches the
   application.
4. **Outbound access works for every source you enabled.** Confirm Horizon's
   hosted containers can actually reach your specific Postgres host, AWS
   region, and New Relic account — this isn't guaranteed by every managed
   platform and wasn't independently confirmed for Horizon at the time this
   doc was written.

## Common pitfalls

- **Server "starts" but nothing can reach it.** `SERVER_HOST` defaults to
  `127.0.0.1`. If you forgot to set it to `0.0.0.0` in Horizon's env vars,
  the process logs success and binds only inside its own container —
  Horizon's gateway can't reach it.
- **An explicit port setting gets silently overridden.** The `$PORT`
  fallback in `core/config.py` is case-insensitive (matching this app's
  `case_sensitive=False` settings config) — `SERVER_PORT`, `server_port`,
  and `Server_Port` are all recognized as "explicitly set." Still, prefer
  leaving `SERVER_PORT` unset entirely on a platform that assigns `$PORT`.
- **CloudWatch tools fail with no `AWS_REGION` fallback.** Only
  `CLOUDWATCH_REGION` is read — legacy `AWS_REGION`/`AWS_ACCESS_KEY_ID`/
  `AWS_SECRET_ACCESS_KEY` env vars are silently ignored (a warning is logged
  if they're set without the `CLOUDWATCH_*` equivalents).

## Rolling back

Horizon deployments are tied to git history — redeploying a previous commit
(or reverting and pushing) rolls the running service back. There's no
in-place stateful data to migrate; this server is read-only against
external sources, so a rollback carries no data-loss risk of its own.
