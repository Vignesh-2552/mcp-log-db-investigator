# Agensi listing: copy/paste text

**Upload:** `support-ticket-investigation.zip`
**Price:** Free
**Suggested category:** Operate (or Agents & Orchestration)

## Title
Support Ticket Investigation (Postgres + CloudWatch + New Relic)

## Short description
Teach your agent a safe discover-then-query workflow for investigating support tickets across Postgres, AWS CloudWatch Logs and New Relic.

## Long description
This skill guides an agent through investigating a customer support ticket using a read-only Investigation MCP server.

**What it teaches**
- Discover before you query: list, then describe, then query, for each source. This avoids queries that silently return zero rows because a field name was guessed.
- Resolving identifiers (store, order, user, request ids) and handling ambiguous matches.
- Tracing a request across the database, CloudWatch and New Relic by correlation id and timestamp.
- Spotting error patterns and infra spikes, and reporting only what the evidence shows.
- Reading the server's structured guardrail errors and self-correcting.

**What's included:** SKILL.md workflow, tool reference, a query cookbook for each source, and example env/MCP client config files (placeholders only).

**Requirements (disclosed up front)**
- The skill does not contain the server. It uses the open-source MIT server `opsinsight-mcp` (https://pypi.org/project/opsinsight-mcp/, source: https://github.com/Vignesh-2552/mcp-log-db-investigator).
- Setup is one block in your MCP client config: the client runs `uvx opsinsight-mcp`, which downloads the server automatically. You need `uv` installed and a read-only Postgres role. AWS and New Relic credentials are optional and stay in your own environment; the skill never asks for credentials in chat.
- The server is read-only (single SELECT only), masks PII, and caps CloudWatch scan cost.

**Works with:** Claude Desktop, Claude Code, Cursor and other MCP clients.

## Tags
support, incident-response, postgres, cloudwatch, new-relic, mcp, observability, debugging
