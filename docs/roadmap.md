# opsinsight-mcp: Roadmap to Enterprise-Grade

Date: 2026-10-28 · Current release: `opsinsight-mcp` 0.1.1 (PyPI), Docker image on GHCR

The next level for this project is to move from "a safe query tool" to "an investigation product teams can trust and deploy". In practice that means four phases:

1. Fix the rough edges found while shipping 0.1.0.
2. Make installation and first-run self-diagnosing.
3. Build the investigation features the design doc still lists as missing.
4. Add the security, audit and operations features a company needs before rolling it out to a team.

## Where the project stands

| Area | Status |
|---|---|
| Tools | 16 read-only tools across PostgreSQL (7), CloudWatch (6), New Relic (3), plus 3 resources |
| Safety | sqlglot AST guardrail, read-only DB sessions, CloudWatch cost ceiling, NRQL guardrail, PII redaction |
| Distribution | `uvx opsinsight-mcp` (stdio, Python 3.11+), Docker image on GHCR, tag-triggered PyPI publishing |
| Quality | Unit tests (guardrails, redaction, services), ruff, CodeQL, CI on every PR |
| Skill | Agensi listing "Support Ticket Investigation", updated for uvx |
| Not built (design doc §4.5, §10 phase 5) | Cross-source `trace_request`, `investigation_start`, MCP prompts, HTTP auth, metrics, audit log |

## Phase 0: Stabilize 0.1.x (release 0.1.1, about 1 week)

Small fixes that remove friction for every new user. Items 0.1–0.5 and 0.8 shipped in 0.1.1 (see `CHANGELOG.md`); 0.6 and 0.7 are still open.

| # | Item | Why | Where |
|---|---|---|---|
| 0.1 ✅ | Non-numeric `NEW_RELIC_ACCOUNT_ID` raises a raw `ValueError` instead of a structured error | Users who leave the placeholder from the example config hit an unhelpful crash | `src/opsinsight/integrations/newrelic/client.py:32`; validate in `Settings` or the service |
| 0.2 ✅ | Add `[project.urls]` (Homepage, Repository, Issues) | The PyPI page has no links back to GitHub; it also helps search ranking | `pyproject.toml` |
| 0.3 ✅ | Set `ENV SERVER_HOST=0.0.0.0 SERVER_PORT=8000` in the Dockerfile | Today every `docker run` needs `-e` overrides, or the port mapping silently fails (app default is 8001) | `Dockerfile`, README |
| 0.4 ✅ | Update `docs/design.md` | It still says Python 3.13+, HTTP-only on `:8000`; stdio and uvx are missing | `docs/design.md` §3, §7, §12 |
| 0.5 ✅ | Add `CHANGELOG.md` and a release checklist | Each PyPI version can be published only once; releases need a repeatable process | repo root, `CONTRIBUTING.md` |
| 0.6 | Repo housekeeping | Delete merged branches; decide on `ci/restrict-push-branches`; make the GHCR package public; set repo topics and website | GitHub settings |
| 0.7 | Run integration tests in CI against a Postgres service container (separate PR: needs a seed SQL file matching the schema the tests expect) | They auto-skip in CI today, so the DB path is only tested by hand | `.github/workflows/ci.yml` |
| 0.8 ✅ | Enable Dependabot (pip, GitHub Actions, Docker) | Dependencies like fastmcp, boto3 and the base image move fast; the recent CA-certificate notice shows why | `.github/dependabot.yml` |

**Done when:** a new user can copy the example config with placeholders, get a clear error for each unconfigured source, and fix it without reading code.

## Phase 1: First-run experience and packaging (release 0.2.0, 2–3 weeks)

The biggest support cost for an MCP server is "it doesn't connect". This phase makes the server explain itself.

1. **`opsinsight-mcp doctor` command.** Checks each configured source and prints a pass/fail table: DB connect plus read-only check, AWS credentials plus allowlisted log groups visible, New Relic key plus account reachable. It never prints secrets. This is the single highest-value usability feature.
2. **`--version` and `--help`** on both entry points.
3. ✅ **Namespace the modules under `opsinsight/`.** Done (unreleased): all code now lives in `src/opsinsight/`, so the wheel installs one top-level package instead of generic `core`/`tools`/`service`/`data` modules.
4. **Startup config summary on stderr.** Log which sources are configured and which are skipped, with secrets masked.
5. **List in the MCP ecosystem.** Publish a `server.json` to the official MCP Registry. Submit to `mcp.so`, `glama.ai` and `smithery.ai`, and open a PR to `awesome-mcp-servers`.

**Done when:** `uvx opsinsight-mcp doctor` tells a new user exactly which variable is wrong, and the package is discoverable outside Agensi.

## Phase 2: Investigation features (release 0.3.0, 3–5 weeks)

These are the features from design doc §4.5 that were planned but never built. They turn 16 separate tools into a guided investigation.

| Feature | What it does | Value |
|---|---|---|
| **MCP prompts**: `investigate_incident`, `trace_user_journey`, `slow_endpoint_rca` | Reusable workflows the client shows as slash commands; each encodes the discover-then-query steps from the skill | Cheapest win. It moves the Agensi skill's workflow into the server, so it works in every client, with or without the skill. |
| **`trace_request`** composite tool | One id plus a time window. It fans out to the DB, CloudWatch and New Relic and returns one merged, timezone-normalized timeline. | Removes the most error-prone manual step: correlation across sources is the client's job today. |
| **`investigation_start`** | Ticket text plus a time window. Returns extracted ids, candidate sources and suggested first queries. | Consistent starting point; fewer zero-row queries |
| **Replica-lag metadata** | Report replica lag in `db_run_query` meta | Prevents false "record missing" conclusions (design doc §11 risk) |
| **Cookbook growth** | Add entries for every repeated bad query seen in practice | The design doc measures this as the main accuracy lever (§4.4) |

Composite tools must keep the existing rules: every sub-query goes through its source's guardrail, results are redacted at the tool boundary, and the response keeps the `{ok, data, meta}` shape.

**Done when:** a ticket like "order 99283 failed at 2pm" resolves with one prompt and one `trace_request` call, instead of a separate inventory, describe and query call for each source.

## Phase 3: Enterprise readiness (releases 0.4–1.0, 1–3 months)

What a security or platform team asks for before approving a shared deployment.

### Security and access

- **Authentication for the HTTP transport.** Use FastMCP's built-in auth support (bearer/JWT or OAuth; confirm the exact provider API at implementation time). Until this exists, a shared HTTP deployment is only safe behind an authenticated proxy.
- **Per-tool and per-source authorization**, for example support staff get DB and logs while only on-call gets metrics.
- **Per-user database roles** where needed, so DB audit logs show the real person, not one shared role.
- **`SECURITY.md`** with a vulnerability-reporting process.

### Audit and observability (design doc §9 "not implemented")

- **Audit log** (JSONL or CloudWatch Logs): who ran which query, when, row count, bytes scanned. Compliance teams will require it.
- **OpenTelemetry metrics and traces** per tool: call count, p50/p95 latency, error rate, guardrail rejections.
- A starter dashboard, plus a weekly guardrail-rejection report to feed the cookbook.

### Supply chain and deployment

- Signed container images and an SBOM (for example cosign and syft in the Deploy workflow).
- PyPI provenance attestations (supported by trusted publishing).
- Deployment templates: a Helm chart and an ECS/Terraform example, with the read-only IAM policy included.
- Pin GitHub Actions to commit SHAs.

### Product polish

- A documentation site on GitHub Pages (already enabled for the repo): setup, tool reference, cookbook, security model.
- A semantic-versioning policy and an upgrade guide.

**Done when (1.0):** a company can deploy one shared server with SSO-backed auth, an audit trail and metrics, from a documented template, and pass a security review.

## Phase 4: Growth (after 1.0)

Pick based on user demand. Each item reuses the existing service, guardrail and tool layering.

- **More data sources:** MySQL (sqlglot already supports the dialect), OpenSearch/Elasticsearch, Grafana Loki, Datadog, Sentry.
- **Ticket-system input:** read the ticket from Zendesk, Jira or Freshdesk by id, so the investigation starts from the real ticket text.
- **Accuracy evaluation suite:** a fixed set of tickets with known root causes, run against each release, to measure whether cookbook and prompt changes actually help.
- **Saved investigations:** export the evidence timeline as a report to attach to the ticket.

## Suggested order

| Order | Item | Effort | Impact |
|---|---|---|---|
| 1 | ~~Phase 0 as release 0.1.1~~ (shipped; 0.6/0.7 open) | Small | High: removes first-run failures |
| 2 | `doctor` command, plus registry and directory listings | Small–medium | High: adoption |
| 3 | MCP prompts | Small | High: the workflow works in every client |
| 4 | ~~Module namespacing~~ (done, unreleased) | Medium | Medium: prevents future breakage; do before 1.0 |
| 5 | `trace_request` | Medium–large | Very high: the core differentiator |
| 6 | HTTP auth and audit log | Medium | Required for any shared or team deployment |
| 7 | Metrics, signed images, Helm | Medium | Enterprise sign-off |

## How to measure progress

| Metric | Source | Target direction |
|---|---|---|
| PyPI downloads per week | pypistats.org | Up |
| Agensi installs and rating | Agensi dashboard | Up |
| GitHub stars and issues opened | GitHub | Up; issue response under 3 days |
| Guardrail rejections per investigation | audit log (Phase 3) | Down, as the cookbook improves |
| Tool calls per resolved ticket | audit log | Down after `trace_request` |
| Accuracy on the eval suite | Phase 4 eval | Up per release |

## Decisions needed

- [x] Approve Phase 0 as release 0.1.1.
- [ ] Is a shared, hosted deployment a goal? If yes, Phase 3 auth moves ahead of Phase 2.
- [ ] Which extra data source do users ask for first?
- [ ] License and support model for enterprise users: MIT only, or a paid support tier?
