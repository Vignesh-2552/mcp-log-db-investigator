# Enterprise Roadmap: OpsInsight MCP

**Written:** 2026-11-17 · **Current version:** 0.3.1 · **Status:** proposal, nothing here is built yet

This document lists what OpsInsight needs before a company can run it for many
teams with real production data. It is ordered: each phase makes the next one
safe to build.

## 1. Where the tool stands today

OpsInsight is a read-only MCP server for support-ticket investigation across
PostgreSQL, AWS CloudWatch and New Relic. It has 17 tools, 3 prompts and
3 resources.

What already works well:

| Area | Today |
|---|---|
| Read-only safety | SQL checked as an AST (sqlglot), re-serialized before it runs; session is read-only with timeouts |
| Data protection | PII and secrets masked in rows, log lines and error messages |
| Cost control | CloudWatch window cap, bytes-scanned ceiling, queries stopped when they run too long |
| Transport | Bearer token, fail-closed on non-loopback hosts, strict Host/Origin checks |
| Operations | Audit log of every call, global rate limit, response size budgets, `doctor` command |
| Quality | 460+ unit tests, about 92% coverage, lint clean |

In review, not merged yet:

- **PR #22**: fixes from the full-project analysis (session hardening, redaction bypasses, NRQL and CloudWatch limits).
- **PR #23**: response cache with optional Redis backend (stacked on #22).

**Merge both before starting this roadmap.** Several items below build on them.

## 2. What is missing for enterprise use

An enterprise buyer asks five questions. Today the answers are:

| Question | Today | Gap |
|---|---|---|
| Who made this call? | One shared token; every caller is `static-token` | No per-user identity |
| Who may see what? | Every caller can use every tool on every table | No roles, no per-team data scope |
| Can we prove what happened? | JSON audit lines on stderr | No durable, tamper-evident store |
| Will it stay up? | Single process, in-memory state | No HA story, no metrics, no SLOs |
| Can we trust the answers? | Cookbook improves accuracy, but nothing measures it | No evaluation suite |

## 3. Roadmap

### Phase 1: Identity and access control (highest priority)

Nothing else is enterprise-ready while every user shares one identity.

| # | Item | What to build | Done when |
|---|---|---|---|
| 1.1 | SSO login (OAuth 2.1 / OIDC) | Replace the shared bearer token with FastMCP's OAuth/JWT provider. Support Okta, Entra ID (Azure AD) and Google. Keep the static token only for local use | Audit events show a real user id; a revoked user loses access within the token lifetime |
| 1.2 | Role-based tool access | Map IdP groups to roles (`viewer`, `investigator`, `admin`). Check the role before each tool call, in one middleware | A `viewer` calling `db_run_query` gets a structured `forbidden` error; the check is covered by tests for every tool |
| 1.3 | Data scoping | Per-role table/schema allowlist for the DB, per-role log-group allowlist for CloudWatch, per-role account for New Relic | A team sees only its own schemas and log groups; `db_list_tables` hides the rest |
| 1.4 | Column-level policy | Config file listing columns that are always masked or always denied, per role. Today masking is by name pattern only | A column can be marked sensitive without renaming it |
| 1.5 | Per-user rate limits | Move from one global bucket to one bucket per user, plus a daily CloudWatch scan budget per user or team | One user's loop cannot block others; budget use appears in `meta` |

### Phase 2: Audit and compliance

| # | Item | What to build | Done when |
|---|---|---|---|
| 2.1 | Durable audit sink | Ship audit events to a pluggable sink: file with rotation, S3 with object lock, or a SIEM (Splunk, Datadog) over HTTPS | Events survive a restart and cannot be edited by the server role |
| 2.2 | Audit content | Add user id, role, source IP, data source touched, tables/log groups read, rows returned, bytes scanned, cache hit | A reviewer can answer "who read the `customer` table last week?" |
| 2.3 | Retention and export | Configurable retention; a CLI command to export a date range | Meets a 1-year retention requirement |
| 2.4 | Redaction policy as config | Move PII patterns and sensitive names from code to a versioned policy file, with regional presets (India, EU, US). Add SSN and IBAN | Adding a pattern needs no code change |
| 2.5 | Compliance pack | Document the controls against SOC 2 and ISO 27001 items; update `docs/security-audit.md` after each release | A security questionnaire can be answered from the docs |

### Phase 3: Reliability and scale

| # | Item | What to build | Done when |
|---|---|---|---|
| 3.1 | Stateless horizontal scaling | Move rate-limit counters and the DB concurrency limit to Redis so limits hold across replicas (the cache from PR #23 already does) | Three replicas behind a load balancer enforce one shared limit |
| 3.2 | Health endpoints | `/healthz` (process up) and `/readyz` (DB, AWS, New Relic reachable), reusing the `doctor` checks | Kubernetes probes work without a tool call |
| 3.3 | Metrics | Prometheus/OpenTelemetry metrics: calls per tool, latency, error rule counts, cache hit rate, bytes scanned, pool usage | A dashboard shows p95 latency and guardrail rejections per tool |
| 3.4 | Tracing | OpenTelemetry spans from tool call to DB/AWS/New Relic call, with the request id | One slow call can be followed end to end |
| 3.5 | Connection resilience | Retries with backoff for AWS and New Relic, a circuit breaker per source, tuned DB pool sizes | A New Relic outage does not slow DB tools |
| 3.6 | Deployment kit | Helm chart, example Terraform, signed container images, image pinned by digest | A new team deploys in under an hour |

### Phase 4: Answer quality

This is what users feel most. It can start in parallel with Phase 1.

| # | Item | What to build | Done when |
|---|---|---|---|
| 4.1 | Evaluation suite | 30 to 50 realistic tickets with expected findings, run against a seeded database and recorded log fixtures. Score tool choice, query correctness and final answer | A score is printed per release; a drop fails CI |
| 4.2 | Integration tests in CI | A seeded Postgres in CI so the integration tests run on every PR (removed from PR #22; needed again here) | `introspect.py` coverage rises from about 31% to above 80% |
| 4.3 | `investigation_start` | The planned composite tool: ticket text and time window in, a structured plan out | The design doc's "not implemented" list is empty |
| 4.4 | Per-team cookbook | Let each team add its own query examples and field-name notes, loaded by role | A team's naming quirks are fixed without a release |
| 4.5 | Smarter correlation | Join error spikes with metrics and deploy events inside `trace_request` | One call answers "what changed before the errors started?" |

### Phase 5: More data sources

The adapter pattern (`TraceSource`, one service per source) already makes this cheap.

| Priority | Source | Why |
|---|---|---|
| 1 | Datadog, Grafana Loki | Most common log stacks after CloudWatch |
| 2 | MySQL, SQL Server | sqlglot already supports the dialects |
| 3 | Sentry, PagerDuty | Error and incident context |
| 4 | Jira, Zendesk (read-only) | Read the ticket itself instead of pasting it |
| 5 | GitHub deployments | Link incidents to releases |

Also publish a short "write your own source adapter" guide so customers can add
internal systems.

### Phase 6: Product and governance

| # | Item | What to build |
|---|---|---|
| 6.1 | Admin console | A small web page to see users, roles, usage, budgets and recent audit events |
| 6.2 | Multi-tenancy | One deployment serving several business units, each with its own credentials and policy |
| 6.3 | Secrets management | Read credentials from AWS Secrets Manager, HashiCorp Vault or Azure Key Vault instead of `.env`; support rotation without restart |
| 6.4 | Release discipline | Version 1.0 with a stable tool contract, a deprecation policy, signed releases and an SBOM |
| 6.5 | Support material | Runbook, upgrade guide, threat model, architecture decision records |

## 4. Suggested order and effort

Effort is a rough guess for one developer working with an AI assistant.

| Order | Work | Effort | Why now |
|---|---|---|---|
| 0 | Merge PR #22 and PR #23 | days | Everything else builds on them |
| 1 | 4.2 integration tests in CI, 4.1 evaluation suite | 1 to 2 weeks | Shows whether later changes help or hurt |
| 2 | 1.1 SSO, 1.2 roles | 2 to 3 weeks | The main enterprise blocker |
| 3 | 2.1 and 2.2 audit sink and content | 1 week | Needed by any security review |
| 4 | 1.3 to 1.5 data scoping, column policy, per-user limits | 2 to 3 weeks | Makes multi-team use safe |
| 5 | 3.1 to 3.4 scaling, health, metrics, tracing | 2 weeks | Needed before a wide rollout |
| 6 | 3.6 deployment kit, 6.3 secrets management | 1 to 2 weeks | Lowers the cost of each new install |
| 7 | Phase 5 sources, Phase 6 console and multi-tenancy | ongoing | Growth work, driven by customer demand |

## 5. Known limits to design around

These cannot be fixed in application code alone. An enterprise install must
handle them at the data source.

- **Boolean probing.** A query can still test a secret's value in a `WHERE`
  clause (`WHERE token LIKE 'a%'`). Only column-level `GRANT`s on the DB role
  stop this. Phase 1.4 should generate the `GRANT` statements for the operator.
- **Phone-like ids.** Ten-digit ids are masked as phone numbers because they
  look the same. Phase 2.4 should let a team exempt named columns.
- **Prompt injection through data.** Log lines and rows can contain
  instructions. All tools are read-only, which limits the damage, but clients
  must still treat tool output as untrusted.
- **The DB role is the last line of defense.** The guardrail is defense in
  depth, not a replacement for a least-privilege role on a replica.

## 6. Definition of "enterprise-ready"

The tool can be called enterprise-ready when all of these are true:

1. Every call is tied to a named user through SSO.
2. Roles limit which tools, tables and log groups each user can reach.
3. Audit events are stored durably, outside the server's control.
4. Three replicas run behind a load balancer with shared limits and cache.
5. Health checks, metrics and traces are exported.
6. An evaluation suite runs on every release and its score is published.
7. Credentials come from a secrets manager, not a file.
8. Version 1.0 is released with a documented, stable tool contract.
