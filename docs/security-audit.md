# Security Audit — opsinsight MCP server

**Date:** 2026-10-28 · **Branch:** `refactor/opsinsight-layout` (uncommitted working tree) ·
**Scope:** `src/opsinsight/**`, `Dockerfile`, `pyproject.toml`/`uv.lock`, `.github/workflows/**`, tests, docs.

**Final status: PASS WITH RISKS.** Every confirmed Critical/High/Medium finding is fixed and
covered by a regression test. The accepted risks are listed in §8.

No secret values appear in this report. A local `.env` holding live credentials was detected
but was never read, printed or committed (§5.4).

---

## 1. Architecture and MCP attack surface

A FastMCP 4.1.3 server (mcp SDK 2.3.0, Starlette/uvicorn) exposing **16 read-only tools** and
schema/cookbook **resources**. Requests flow `tools/*` → `service/*` → `integrations/<source>/guardrail.py`
→ client/engine. There are three backends:

| Backend | Client | Guardrail |
|---|---|---|
| Postgres (hot-standby replica) | SQLAlchemy + asyncpg | sqlglot AST validation, function denylist, LIMIT clamp, session `default_transaction_read_only`, statement timeout |
| AWS CloudWatch | boto3 Logs Insights / Metrics | log-group allowlist, window cap, bytes-scanned ceiling, `SOURCE` rejection |
| New Relic | NerdGraph (httpx) | regex NRQL guardrail over masked literals, window cap |

**Attack surface**

| Entry point | Who can reach it | Controls (after this audit) |
|---|---|---|
| Streamable HTTP `/mcp` | Anything that can route to `SERVER_HOST:SERVER_PORT`, including browsers through DNS rebinding | Bearer token (`MCP_AUTH_TOKEN`, ≥32 chars, constant-time compare); fail-closed start on non-loopback hosts; strict Host/Origin allowlist |
| stdio | The local process that launched the server | OS process boundary |
| Tool arguments (SQL, Insights, NRQL, identifiers) | The client model, and therefore anything that can prompt-inject it | Per-source guardrails, size caps, row limits, timeouts |
| Tool output (DB rows, log lines) | Returned to the model | PII and secret redaction at the tool boundary |
| Container image | Deployment | Non-root user (uid 10001) |
| CI (`.github/workflows`) | Repo contributors and Actions supply chain | Actions still referenced by mutable tags (F11, open) |

## 2. Tool inventory and authorization matrix

All tools are read-only. None writes, executes commands, touches the file system or makes
egress calls to user-chosen hosts. `tests/unit/test_audit.py` pins this inventory.

| Tool | Reads | Side effects | Network/DB |
|---|---|---|---|
| `db_list_tables` | Catalog | none | Postgres |
| `db_describe_table` | Catalog | none | Postgres |
| `db_run_query` | Arbitrary guarded `SELECT` over production replica data | none (read-only session) | Postgres |
| `db_explain_query` | Plan for a guardrail-validated query (`EXPLAIN (FORMAT JSON)`, no `ANALYZE`, so the query is not executed) | none | Postgres |
| `db_sample_rows`, `db_resolve_store`, `db_search_by_identifier` | Bounded, parameterized lookups over replica data | none | Postgres |
| `cw_list_log_groups` | Allowlisted group names (none if no allowlist) | none | AWS |
| `cw_describe_log_fields` | Field discovery on allowlisted groups | none | AWS (billed per GB scanned) |
| `cw_run_insights_query`, `cw_get_trace_events` | Allowlisted log groups | none | AWS (billed, capped) |
| `cw_filter_events` | Allowlisted log groups, messages redacted | none | AWS |
| `cw_get_metric_stats` | Any CloudWatch metric the IAM role can read | none | AWS |
| `nr_list_event_types`, `nr_describe_log_fields`, `nr_run_nrql_query` | New Relic account data | none | New Relic |

**Authorization model.** Access is enforced at the transport, with one shared token, so
every authenticated client can call every tool and read every resource. There are no
per-tool scopes, per-user identities or tenant isolation; this is accepted risk R1 (§8).
Data reach is limited by the backend credentials: the DB role, the IAM policy and the NR key
scope. Those are the real authorization boundary and must be least-privilege (§9). The model
is **not** relied on to enforce any of these controls, and schema validation of tool
arguments is not treated as a security control.

## 3. Scans

Every command below was actually run. Scanners ran locally or in local Docker. No code was
uploaded, and semgrep ran with `--metrics=off`.

| Tool | Command | Before fixes | After fixes |
|---|---|---|---|
| pip-audit | `uv export --frozen --no-hashes \| pip-audit -r /dev/stdin` | **27 advisories**, all transitive: authlib ×7, pyjwt ×14, urllib3 ×3, dnspython ×1, opentelemetry-api ×1 | **No known vulnerabilities found** (after `uv lock --upgrade-package` for those five packages) |
| bandit | `bandit -r src -q` | B324 sha1, B608 ×3 | B105 ×N (placeholder strings), B608 ×4: all false positives (§5) |
| semgrep | `semgrep --config p/python --config p/security-audit --config p/secrets --config p/dockerfile --config p/github-actions --metrics=off` | 17 | 16: 13 mutable action tags (F11, open), 2 `avoid-sqlalchemy-text` (FP), 1 sha1 (FP) |
| gitleaks (history) | `gitleaks git --redact` | 39 commits, **0 leaks** | 0 leaks |
| gitleaks (tree) | `gitleaks dir --redact` | 3 hits in the gitignored local `.env` (never committed); synthetic fixtures in tests | Test fixtures annotated `gitleaks:allow`. Remaining hits: `.env` and gitignored `__pycache__/*.pyc` |
| ruff | `uv run ruff check .` | — | **All checks passed** |
| pytest unit | `uv run pytest tests/unit -q` | — | **216 passed** |
| pytest integration | `uv run pytest tests/integration -m integration -q` | 1 failed, 5 passed | **1 failed, 5 passed**: the same pre-existing failure, unrelated to security (§7) |

## 4. Findings

Status key: **Confirmed** = reproduced; **Potential** = plausible but not demonstrated.

### High

**F1 — Unauthenticated HTTP endpoint, exposed to DNS rebinding in Docker** · Confirmed · **Fixed**
- *Location:* `server.py`, `Dockerfile` (`SERVER_HOST=0.0.0.0`).
- *Evidence:*
  - The server had no auth.
  - FastMCP's default `host_origin_protection="auto"` only validates Host/Origin when the bind address is loopback.
  - A live process on `0.0.0.0` answered a request with `Host: attacker.example` with **200**.
- *Scenario:* a victim visits a malicious page whose hostname rebinds to 127.0.0.1. The page then calls `db_run_query` against the published port and exfiltrates production data.
- *Fix:*
  - `core/transport_security.py`: a bearer-token verifier (≥32 chars, `hmac.compare_digest`).
  - The server refuses to start on a non-loopback host without a token, unless `ALLOW_UNAUTHENTICATED_HTTP=true`.
  - Always-strict Host/Origin checks, with extra hosts set through `SERVER_ALLOWED_HOSTS` / `SERVER_ALLOWED_ORIGINS`.
- *Verified:*
  - Live process: no token → 401, wrong token → 401, rebinding Host → 421, valid → 200.
  - Docker image without a token exits 1.
  - Tests in `tests/unit/test_transport_security.py`.

**F2 — SQL guardrail bypasses** · Confirmed · **Fixed**
- *Location:* `integrations/database/constants.py`, `guardrail.py`.
- *Evidence:* the following all passed validation before the fix:
  - `dblink_exec` / `dblink_send_query`, `query_to_xml`.
  - `pg_sleep_for` / `pg_sleep_until`, `pg_advisory_lock`.
  - `SELECT … INTO`, `FOR UPDATE`.
  - `set_config`, `nextval`.
  - `lo_get`, `pg_stat_file`, `pg_ls_logdir`, `pg_reload_conf`, `pg_logical_emit_message`.
  - Quoted `"pg_sleep"`.
- *Impact:*
  - `dblink` opens a new connection that escapes the read-only session.
  - The file/log readers leak server data.
  - The sleep and lock functions allow DoS.
  - `nextval` mutates state.
  - Actual damage depends on the DB role, which is the last line of defense.
- *Fix:*
  - Blocked `exp.Into` / `exp.Lock` nodes.
  - Expanded the exact-name denylist and added a prefix denylist (`dblink`, `pg_sleep`, `pg_advisory`, `lo_`, `pg_ls_`, `pg_logical_`, …).
  - Case-normalized anonymous function names.
  - `MAX_SQL_CHARS`.
- *Verified:* new cases in `tests/unit/test_db_guardrail.py`. Over stdio, `SELECT INTO` returns `blocked_operation` and `dblink_exec` returns `blocked_function`.

### Medium

**F3 — Secrets in DB rows and log text not redacted** · Confirmed · **Fixed**
- PII redaction existed, but credentials passed through unchanged:
  - `password=…`, `Authorization: Bearer …`, AWS key IDs, `NRAK-` keys, JWTs, PEM keys.
  - Columns like `api_key` and `db_password`.
- *Fix:*
  - `core/redaction.py` gained secret patterns and key=value masking that keeps the key name and quotes.
  - `is_sensitive_key()` masks columns by substring.
  - CloudWatch Insights rows now go through `redact_row`.
- Tests: `test_redaction.py`, `test_cloudwatch_tools.py`.

**F4 — No audit trail of tool calls** · Confirmed · **Fixed**
- *Fix:* `core/audit.py` `AuditMiddleware` logs one JSON event per tool call or resource read:
  - actor, request id, arguments (redacted, truncated to 2 000 chars), outcome, guardrail rule, row count, duration.
  - JSON encoding prevents log-line injection.
  - Tokens are never logged; a live check found 0 occurrences of the token in the logs.
- Tests: `tests/unit/test_audit.py`.

**F5 — Vulnerable transitive dependencies** · Confirmed · **Fixed**
- 27 advisories across authlib, pyjwt, urllib3, dnspython and opentelemetry-api.
- Upgraded in `uv.lock` to authlib 1.9.1, pyjwt 2.15.1, urllib3 2.8.0, dnspython 2.9.0, opentelemetry-api 1.45.1.
- pip-audit is now clean.

**F6 — Container ran as root** · Confirmed · **Fixed**
- `Dockerfile` adds a system user `app` (uid 10001) and `USER app`.
- Verified with `id` inside the built image.

### Low

**F7 — `cw_list_log_groups` enumerated every log group when no allowlist was set** · Confirmed · **Fixed**
- It now returns an empty list plus a note, without calling AWS.

**F8 — The Logs Insights `SOURCE` command could name log groups outside the allowlist** · Potential · **Mitigated**
- `validate_insights_query` now rejects a leading `SOURCE` command (`source_command_not_allowed`).
- `SOURCE` inside a filter string is still allowed.

**F9 — `DB_URL` (contains a password) was a plain `str` setting** · Confirmed · **Fixed**
- It is now a `SecretStr`, so it can't leak through `repr` or settings dumps.

**F10 — Unbounded query text size** · Confirmed · **Fixed**
- Caps added: SQL 50 000, Insights 10 000 and NRQL 20 000 characters (`query_too_long`).

**F11 — GitHub Actions referenced by mutable tags** · Confirmed · **Open**
- semgrep reports 13 instances. Fix: pin each action to a full commit SHA.
- Not changed here, because it alters CI behavior and needs the maintainer to choose the SHAs.

## 5. False positives and verified-safe items

| Item | Why it's not a vulnerability |
|---|---|
| bandit B608 / semgrep `avoid-sqlalchemy-text` (introspection, inventory queries) | Identifiers pass through `quote_ident`, or are regex-validated before interpolation; values are bound parameters |
| bandit B608 in the NR event-type query | Event type is regex-validated (`^[A-Za-z_][A-Za-z0-9_]*$`) |
| bandit B324 / semgrep sha1 | A cache key, not a security use; now marked `usedforsecurity=False` |
| bandit B105 | Placeholder strings and constant names, not credentials |
| gitleaks hits in `tests/` | Synthetic redaction fixtures, annotated `gitleaks:allow`; the `.pyc` copies are gitignored |
| Raw `EXPLAIN [ANALYZE]` via `db_run_query`, stacked statements, data-modifying CTEs | Probed; already rejected before this audit. `db_explain_query` never adds `ANALYZE` |

### 5.4 Local `.env`
gitleaks flagged 3 credentials in the working-tree `.env`.
- The file is gitignored, absent from all 39 commits and excluded by `.dockerignore`.
- Its values were not read or printed during the audit.
- No exposure occurred, so rotation is not required. Rotate if the machine is shared or the file was ever copied elsewhere.

## 6. Files changed

**New:** `src/opsinsight/core/transport_security.py`, `src/opsinsight/core/audit.py`,
`tests/unit/test_transport_security.py`, `tests/unit/test_audit.py`, `docs/security-audit.md`.

**Modified:**
- Code:
  - `core/app.py` (registers the middleware), `core/config.py`, `core/redaction.py`, `server.py`.
  - `integrations/database/{constants,guardrail}.py`, `integrations/cloudwatch/{constants,guardrail}.py`, `integrations/newrelic/{constants,guardrail}.py`.
  - `service/cloudwatch_service.py`.
- Build: `Dockerfile`, `uv.lock`.
- Tests: `tests/unit/test_db_guardrail.py`, `test_redaction.py`, `test_cloudwatch_tools.py`.
- Docs: `README.md`, `CHANGELOG.md`, `CLAUDE.md`, `.env.example`, `docs/design.md`, `docs/tool-reference.md`, `docs/agensi-skill/.../{setup.md, env.example, mcp-config.http.example.json}`.

**Not part of this audit:** the `.gitignore` change in the working tree (removal of `.vscode/`)
predates the audit edits and was left untouched for the owner to review.

**Breaking changes** (also listed in `CHANGELOG.md`):
- HTTP on a non-loopback host now requires `MCP_AUTH_TOKEN`.
- Requests with a non-allowlisted Host/Origin are rejected.
- The added SQL functions and `SOURCE` are now refused.
- With no allowlist, `cw_list_log_groups` returns nothing.

## 7. Tests

| Suite | Result |
|---|---|
| `uv run ruff check .` | All checks passed |
| `uv run pytest tests/unit -q` | **216 passed** |
| `uv run pytest tests/integration -m integration -q` (real Postgres replica) | **5 passed, 1 failed** |
| Live HTTP checks (real `0.0.0.0` process, tokened) | 401 / 401 / 421 / 200 as expected; 16 tools listed; guardrail rejections returned as structured errors |
| Live stdio checks | 16 tools; guardrail and audit events confirmed |
| Docker image | Runs as uid 10001; exits 1 without a token; 401 without a token, 200 with one |

The one integration failure, `test_db_search_by_identifier_finds_order_and_payment`, predates
the audit. `MAX_SEARCH_TARGETS=25` truncates the candidate columns before the payment table is
reached, so it is a search-ranking issue, not a security one. It still needs an owner decision:
raise the cap, rank better, or change the test.

## 8. Unresolved issues and accepted risks

| ID | Severity | Item | Why open |
|---|---|---|---|
| F11 | Low | Mutable Action tags | Needs maintainer to pin SHAs |
| R1 | Info (design) | One shared token: no per-user identity, per-tool scopes or tenant isolation | Needs an IdP (OAuth/JWT); roadmap Phase 3 |
| R2 | Info | Prompt injection through returned rows and log text | Mitigated: all tools are read-only, with no write or egress tools. Clients should still treat tool output as untrusted |
| R3 | Info | `cw_get_metric_stats` reads any metric the IAM role allows | Scope the IAM policy |
| R4 | Info | AWS/NR error messages are returned to the client | Useful for self-correction; they contain resource names, not credentials |
| R5 | Info | pyjwt PYSEC-2026-4146 has no upstream fix | Not reachable: the server does no JWT validation |
| R6 | Info | Base image not pinned by digest | Pin it in the release pipeline |
| R7 | Info | Audit events share stderr with app logs; no tamper-evident sink | Route the `investigation_server.audit` logger to a SIEM |
| R8 | Info | `db_resolve_store` ILIKE wildcards are user-controllable | Gives no privilege gain; bounded by LIMIT and timeouts |

**Not reviewed:**
- FastMCP and mcp SDK internals beyond the Host/Origin and auth code paths used here.
- The deployed infrastructure: network policy, the real DB role grants, the IAM policy, the NR key scope. These were out of reach and must be checked by the operator.
- The `docs/agensi-skill` content, beyond its config examples.

## 9. Required manual actions and deployment recommendations

1. Set `MCP_AUTH_TOKEN` (≥32 random chars, e.g. `secrets.token_urlsafe(32)`) in every HTTP deployment and store it in a secret manager.
2. Set `SERVER_ALLOWED_HOSTS` / `SERVER_ALLOWED_ORIGINS` for any non-localhost hostname.
3. Connect with a **read-only DB role** that has no `dblink`, `lo_*`, file-access or `pg_read_server_files` privileges, ideally restricted to the needed schemas.
4. Scope the IAM policy to the allowlisted log groups and the required metrics. Use a New Relic user key with the minimum account access.
5. Pin GitHub Actions to commit SHAs (F11) and the base image to a digest (R6).
6. Ship `investigation_server.audit` events to durable storage.
7. Keep `.env` out of git and images, as it is now. Rotate its credentials if the machine is shared.
8. For multi-user access, move from the shared token to OAuth/JWT with per-tool scopes (R1).

## 10. Summary

| Severity | Total | Confirmed | Fixed / mitigated | Remaining |
|---|---|---|---|---|
| Critical | 0 | 0 | 0 | 0 |
| High | 2 | 2 | 2 | 0 |
| Medium | 4 | 4 | 4 | 0 |
| Low | 5 | 4 (+1 potential) | 4 | 1 (F11) |
| Info / accepted | 8 | — | — | 8 (R1–R8) |
| **Total** | **19** | **10 (+1 potential)** | **10** | **9** |

**Status criteria:**
- **PASS** = no open findings.
- **PASS WITH RISKS** = no open Critical/High/Medium; Low/Info items documented with owners.
- **FAIL** = any open Critical/High.

Result: **PASS WITH RISKS**.
