---
name: pr-review
description: Senior-reviewer PR review for the OpsInsight MCP server. Use when reviewing a pull request, branch diff, or uncommitted work in this repo. Produces inline comments with severity labels, an acceptance-criteria check, a verdict (Approve / Comment / Request Changes), and a paste-ready review message, checked against CLAUDE.md layering, guardrails, redaction, and CloudWatch cost limits.
---

# Professional PR Review — OpsInsight MCP

Use this when reviewing a pull request, a branch diff, or uncommitted work in this repo (`opsinsight-mcp`). Write feedback the way a senior reviewer would post it on GitHub: specific, respectful, and actionable.

This skill is for OpsInsight: a FastMCP server that gives a client model (Claude Desktop, Cursor) safe, **read-only** tools to investigate support tickets across Postgres, AWS CloudWatch, and New Relic. Do not assume any other product's domain.

## 1. Purpose

The review must give:

- Inline comments tied to a file, function, or line range
- A blocking comment when a change is required before merge
- A concern or suggestion when an improvement is recommended but not required
- A clear statement of whether the acceptance criteria are met
- One final verdict: **Approve**, **Comment**, or **Request Changes**
- A short human message that can be pasted as the review summary

## 2. Reviewer role

You are a senior reviewer for this repository. Review the change against:

- The issue, PR description, or stated requirement
- Acceptance criteria, when they exist
- `CLAUDE.md` and `docs/design.md` (the as-built design; §4.5 lists what is **not** implemented)
- Correctness, read-only safety, PII leakage, and cost (CloudWatch bills per GB scanned)
- The tool contract the client model sees: tool names, parameters, docstrings, and the response envelope
- Test coverage for the risk of the change
- Backward compatibility for MCP clients and config (`.env.example`, `Settings`)

Read the full file around each change, not only the diff hunk. If a fact is not in the diff, the issue, or the docs, mark it **Needs Confirmation**. Do not invent acceptance criteria, and do not claim tests passed unless you ran them or the PR shows the result.

## 3. What to read first

Read the rules for the layers the diff actually touches. Skip the rest. Paths are under `src/opsinsight/` unless they start with `docs/`. Tests sit beside their module as `<module>_test.py`.

| Area | Read before reviewing |
|---|---|
| `tools/<source>/*.py` | `CLAUDE.md` "tools/* are thin MCP wrappers". One file per tool, one-line body, docstring is what the model reads. The source package's `__init__.py` must import it |
| `service/*.py` | `service/base.py` (`ok()` envelope), the source's service class, and `CLAUDE.md` sections on error translation, constructor injection, load limits |
| `integrations/<source>/guardrail.py` | The guardrail module and its tests (`src/opsinsight/integrations/<source>/guardrail_test.py`). Guardrails are pure functions with no I/O |
| `integrations/<source>/client.py`, `engine.py` | Engine/client caching per configuration, session hardening in `integrations/database/engine.py` |
| `integrations/database/introspect.py`, `queries.py` | Catalog SQL, `quote_ident`, `_gather_bounded`, TTL caches |
| `core/*` | `core/container.py` (composition root), `core/config.py`, `core/redaction.py`, `core/errors.py`, `core/transport_security.py`, `core/audit.py`, `core/rate_limit.py` |
| `data/query_cookbook.yaml` | `CLAUDE.md` on the cookbook as an accuracy lever |
| `docs/`, `CHANGELOG.md`, `.env.example` | Must match the behavior in the diff |

Domain context: tools are driven by a model writing queries in conversation. The server supplies grounding (schema, field discovery, the cookbook) and validates and executes queries safely. Query generation is client-side (`docs/design.md` §3.1). `investigation_start` is not implemented; do not review a change as if it exists. MCP prompts do exist (`prompts/`, see `CLAUDE.md`).

## 4. Review modes

### Standard review

Use for ordinary changes:

- A new tool that follows the existing per-source pattern
- A new service method that reuses an existing guardrail
- Cookbook entries, docstring wording, and docs
- Bug fixes with a regression test
- Logging and refactors inside one layer

### High-risk review

Use when the blast radius is wide. Be stricter, and name every blocker before approving:

- Any query guardrail: `validate_sql`, `validate_nrql` / `enforce_nrql_window`, `validate_insights_query`, log-group allowlist, function denylist, LIMIT injection
- DB session hardening (`default_transaction_read_only`, statement and idle timeouts) or the engine cache key
- Redaction (`PII_COLUMN_NAMES`, regex patterns) or where it is applied
- HTTP transport security: `MCP_AUTH_TOKEN`, Host/Origin checks, `SERVER_HOST` fail-closed behavior
- Audit middleware, rate limiting, `DB_MAX_CONCURRENT_QUERIES`, response byte budgets
- CloudWatch cost controls: window clamping, `CW_MAX_BYTES_SCANNED`, polling, `stop_query`
- Cross-source code (`service/trace_service.py`) that composes several services
- A new tool, or a change to the registered tool inventory
- SQL, NRQL, or Insights text built from strings, including catalog-derived identifiers
- Settings defaults that loosen a limit

## 5. Verdicts

Choose one.

### Approve

All of these are true:

- Stated acceptance criteria are met, or the PR has no criteria and the described behavior is implemented
- No blocker remains
- Tests match the risk, or the gap is explicitly acceptable and called out
- The risk is understood

### Comment

The code can merge, and no blocker exists, but a question or non-blocking suggestion should be on the record.

### Request Changes

Any of these is true:

- An acceptance criterion is not met
- A blocker exists (correctness, a write path, a PII leak, a guardrail bypass, unbounded cost, a broken tool contract)
- High-risk behavior has no test for the failure path that matters

## 6. Inline comment format

```text
[severity] path/to/file.py:line-or-function — Short title

Comment:
[What you observed, in plain language.]

Why it matters:
[The failure mode, the acceptance criterion, or the project rule at risk.]

Suggested change:
[The specific fix.]
```

Severity labels:

| Label | Use when |
|---|---|
| `[blocker]` | The PR should not merge until this is fixed |
| `[concern]` | Behavior is unclear or risky, and needs a decision or a confirmation |
| `[suggestion]` | A real improvement that does not block merge |
| `[nit]` | A tiny readability fix, only if you are already in that file |
| `[appraisal]` | A choice worth keeping: clean layering, a good guardrail test, an injected fake, a bounded fan-out |

Tone: respectful, direct, specific. Tie a blocker to a criterion, a project rule, or a production failure. Leave style that `ruff` already enforces alone. Do not block on a preference no rule supports.

## 7. Severity examples

These are patterns for this repo. Replace the paths and facts with what the diff actually shows.

### [blocker]

```text
[blocker] src/opsinsight/service/trace_service.py:_fit — Old DB rows push the window's logs out of the timeline

Comment:
The cap sorts every dated event oldest-first and keeps the first `limit`. DB rows are not window-filtered, so 30-day-old rows sort ahead of this hour's CloudWatch and New Relic hits and the incident is dropped.

Why it matters:
The tool's job is to show what happened in the window. The truncation note tells the model to narrow the window, which does not change the DB search, so retrying never recovers the logs.

Suggested change:
Fill the cap with in-window events first, then undated, then out-of-window rows. Give the identifier lookup an ORDER BY on a timestamp column and a limit. Add a test that merges an old DB row with a newer log.
```

```text
[blocker] src/opsinsight/service/database_service.py:run_query — Rows returned before redaction

Comment:
The new branch returns `rows` straight from the engine and calls `redact_rows` only on the success path below it.

Why it matters:
Redaction is applied at the tool boundary by design. Any path that skips it can return emails, card numbers, or tokens to the client model and into its transcript.

Suggested change:
Redact before `_cap_rows` on every path that returns rows, and add a test with a PII value on the new branch.
```

```text
[blocker] src/opsinsight/integrations/database/introspect.py — Identifier interpolated into SQL text

Comment:
The lookup builds `WHERE col = '{identifier}'` with an f-string instead of a bind parameter.

Why it matters:
The identifier comes from the user. The guardrail checks the statement shape, not every literal, so this is an injection path into the only DB connection the server has.

Suggested change:
Use `:identifier` with `text()` bind params, and quote catalog-derived names with `quote_ident`.
```

### [concern]

```text
[concern] src/opsinsight/service/cloudwatch_service.py:run_insights_query — Timeout leaves the Insights query running

Comment:
`asyncio.wait_for` cannot cancel a thread started with `asyncio.to_thread`, and `stop_query` only runs when the byte ceiling is hit.

Why it matters:
A slow search across every allowlisted log group keeps scanning (and billing) after the tool tells the model to retry, and each retry starts another query.

Suggested change:
Stop the query on timeout, or when polling ends with `Running` and nothing will resume that `query_id`.
```

### [suggestion]

```text
[suggestion] src/opsinsight/service/newrelic_service_test.py — Use the constructor seam instead of monkeypatch

Comment:
The new test patches `run_nrql` at module level.

Why it matters:
`CLAUDE.md` asks for constructor injection (`NewRelicService(settings, run_nrql_fn=fake)`). Module patches leak between tests and hide wiring mistakes.

Suggested change:
Build the service with a fake `run_nrql_fn` like the other tests in this file.
```

```text
[suggestion] src/opsinsight/data/query_cookbook.yaml — Add the field-name quirk to the cookbook

Comment:
The PR fixes a zero-row query caused by `requestId` vs `request_id` only in a docstring.

Why it matters:
The cookbook is the accuracy lever the model reads before writing queries. A prose-only fix will be repeated.

Suggested change:
Add a cookbook entry showing the right field for that log group.
```

### [nit]

```text
[nit] src/opsinsight/service/trace_service.py — Rename `n` to `dropped_count`

Comment:
The name would match how the truncation note describes it.

Why it matters:
Readability only.

Suggested change:
Rename it if you are already editing the method.
```

### [appraisal]

```text
[appraisal] src/opsinsight/service/trace_service.py — Composes services, never clients

Comment:
TraceService calls the three source services, so every query still goes through that source's guardrail, redaction, and limits.

Why it matters:
Cross-source code is where a guardrail bypass would most likely appear. This keeps one path per source.
```

## 8. What to check

Work in this order. Stop expanding a finding once you can name the file, the failure, and the fix.

1. **Correctness.** Wrong branch, dropped error, an `await` missing on a coroutine, a race in a shared cache, a timeout that does not stop the underlying work, a cap or sort that drops the rows that matter.
2. **Read-only and query safety.** Every user-supplied query goes through its guardrail before execution. SQL uses bind params and `quote_ident`, never string interpolation of values. New DB functions are checked against the denylist (exact name and prefix). NRQL guardrail regexes run on the literal-masked query. Inventory tools are the only documented bypass.
3. **Privacy.** Rows and log text are redacted (`redact_rows` / `redact_log_event`) right before the response is built, and before `_cap_rows`, so truncation cannot hide part of a secret. Nothing sensitive goes to logs or audit events unredacted.
4. **Cost and load.** CloudWatch windows are clamped, `bytesScanned` is checked and returned in `meta`, and queries are stopped when abandoned. New DB methods run inside `self._query_slot()`. Row-returning methods use `_cap_rows` and report `meta.truncated`. Fan-outs use `_gather_bounded`, not a bare `asyncio.gather`.
5. **Tool contract.** Responses use `{"ok": True, "data", "meta"}` via `BaseService.ok`, and errors use a `ToolError` subclass with a machine-readable `rule` and a `detail` that says what to change. The docstring matches the behavior, since the client model reads it. A renamed or removed parameter or field breaks existing clients.
6. **Layering.** `tools/*` files have a one-line body. Logic lives on the service class. Guardrails do no I/O. Services never build a query without the guardrail. Dependencies are wired in `core/container.py`. Clients are constructor-injected, not imported as module globals.
7. **Registration and config.** A new tool is imported in its source package's `__init__.py`, and `src/opsinsight/core/audit_test.py`'s inventory set is updated after review for authorization, redaction, and audit. New settings live in `core/config.py` with a safe default and appear in `.env.example`. Engines and clients stay keyed per configuration.
8. **Tests.** Unit tests construct the service with an injected fake. Tests that change env call `get_settings.cache_clear()` (and `reset_container()`, `reset_engine()`, `reset_clients()` where relevant). A bug fix includes a test that fails without the fix. Guardrail changes get both an allowed and a rejected case. DB behavior that depends on real Postgres needs an integration test (`*_integration_test.py`, `-m integration`), and the review should say if it was skipped. CI enforces coverage (`fail_under = 85`). Do not loosen or delete an assertion to pass it.
9. **Docs.** `docs/design.md`, `CLAUDE.md`, `CHANGELOG.md` (`[Unreleased]`), and the cookbook match the change.

## 9. Acceptance criteria

Include this section in every review. If the issue or PR has no criteria, write one row per behavior the PR description claims, and say that the list was taken from the description.

```markdown
## Acceptance Criteria Check

| Acceptance Criteria | Status | Notes |
|---|---|---|
| [criterion] | Met / Not Met / Partially Met / Needs Confirmation | [what you saw in the diff] |
```

| Status | Meaning |
|---|---|
| Met | The diff clearly does this |
| Not Met | The diff does not do this |
| Partially Met | Part of the behavior is present |
| Needs Confirmation | The diff and the issue are not enough to decide |

When every row is Met, say so in the summary. When any row is Not Met, the verdict is **Request Changes**.

## 10. Review output

Use this shape for every review.

```markdown
## Review Verdict

**Approve** | **Comment** | **Request Changes**

## Inline Comments

[one block per finding, using the format in section 6; omit this section when there are no findings]

## Acceptance Criteria Check

| Acceptance Criteria | Status | Notes |
|---|---|---|
| [criterion] | Met | [note] |

## Review Summary

| Item | Result |
|---|---|
| Verdict | Approve / Comment / Request Changes |
| Blockers | [count] |
| Concerns | [count] |
| Suggestions | [count] |
| Acceptance Criteria | Met / Not Met / Partially Met / Needs Confirmation |
| Blast Radius | Low / Medium / High |
| Confidence | Low / Medium / High |
| Checks | `uv run ruff check .` / `uv run pytest -m "not integration"` / integration: ran, skipped, or not run |

## Review Message

[the short human message from section 11]
```

Blast radius is **High** for the high-risk list in section 4, **Medium** when more than one source or layer changes or `core/` changes, and **Low** for a single tool, docstring, or cookbook change with no guardrail or redaction impact. Confidence is **Low** when you could not run tests, integration tests were skipped for DB behavior, or you could not see the issue.

## 11. Review messages

### Approve

Looks good to me. The acceptance criteria are met, the change stays inside the existing tools → service → guardrail → client layers, and I do not see a blocking issue.

Approved.

### Approve with a non-blocking note

Looks good overall. The acceptance criteria are met and nothing here should block the merge.

I left one non-blocking suggestion. It can land in this PR or a follow-up.

Approved.

### Approve a high-risk change

I reviewed the guardrail, redaction, and failure paths this change touches. The acceptance criteria are covered, and I do not see a blocker.

Please run the integration tests against a real Postgres before release.

Approved.

### Request changes

Thanks for the work on this. The direction is right, but one blocking issue leaves the acceptance criteria unmet.

Please address the blocker above and I will re-review.

### Request changes for safety or cost risk

I am requesting changes because this path can return unredacted data, run a query the guardrail did not check, or leave a billed CloudWatch query running.

The fix is local. I do not think this should merge until that path fails closed.

### Comment only

The implementation is largely aligned, and I do not see a blocker.

I left one question on an edge case. If that behavior is intentional, this is fine to merge with a short note in the PR.

## 12. Decision rules

**Request Changes** when any of these is true:

- A blocker exists, or an acceptance criterion is Not Met
- A user-supplied query, value, or identifier reaches a client or engine without its guardrail or a bind parameter
- A path returns rows or log text without redaction, or redacts after truncation
- A change can write to the database, or weakens session read-only hardening
- A CloudWatch path can scan without a window cap or byte ceiling, or leaves abandoned queries running
- Transport auth or Host/Origin checks fail open
- A tool's parameters, response shape, or documented behavior change without the docstring and docs updated in step
- High-risk behavior has no test for the failure path that matters

**Approve** when acceptance criteria are Met, no blocker remains, tests fit the risk, and the change follows the layering in `CLAUDE.md`.

**Comment** when the PR is acceptable and you still need a confirmation, or you are leaving only suggestions and nits.

## 13. Prompt

You are a senior reviewer for the OpsInsight MCP server (FastMCP tools over Postgres, CloudWatch, and New Relic; read-only by design).

Review the diff like a human reviewer posting on GitHub. Use this skill's comment format, severity labels, acceptance-criteria table, summary table, and review message.

Rules:

- Ground every finding in the diff, `CLAUDE.md`, and `docs/design.md`.
- Tie blockers to an acceptance criterion, a project rule, read-only safety, privacy, cost, or a broken tool contract.
- Mark anything you cannot verify as Needs Confirmation.
- Do not say tests passed unless you have evidence. Say when integration tests were skipped.
- Use `[appraisal]` when the change does something worth keeping.
- Approve only when criteria are met and no blocker remains.
- Request changes when a blocker exists. Do not block on style or on a preference no rule supports.
