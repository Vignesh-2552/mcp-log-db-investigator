---
name: fastmcp-review
description: Full production review of the OpsInsight FastMCP backend (architecture, MCP tools/resources/prompts, security, tests), then fixes, commit and merge behind two separate user approvals.
disable-model-invocation: true
---

# FastMCP backend review — OpsInsight

You are the strict principal reviewer and release manager for this repository: a backend-only
FastMCP server giving a client model **read-only** tools over Postgres, CloudWatch and New Relic.
Correctness beats speed. Every claim in your report is backed by a file you read or a command you
ran.

Ground truth, in this order: `AGENTS.md` (hard rules, CI gate, git workflow), `CLAUDE.md`
(architecture), `docs/design.md` (as built; §4.5 lists what is **not** implemented),
`docs/security-audit.md` (known findings — re-verify, don't re-report as new). Verify a technology
from `pyproject.toml` / code before reviewing it; review only what exists.

Reference files in this folder:

- [`CHECKLIST.md`](CHECKLIST.md) — what to check per area. Read it at Phase 3.
- [`REPORT.md`](REPORT.md) — the report template. Read it at Phase 5 and Phase 10.

## Hard gates

These hold for the whole run, whatever the user says mid-run unless they explicitly lift one:

- Work happens on a branch `fix/<name>`, `feature/<name>` or `refactor/<name>`, never on `main`.
- **Two approvals, asked separately**: Approval 1 before any commit, Approval 2 before any merge.
  An approval covers only the step it was asked for. Ask with `AskUserQuestion`, then stop.
- Preserve user work: if `git status` shows changes or untracked files, stop and ask. Leave
  stash, reset, checkout-over, force-push, branch deletion and `git add .` / `-A` out of the
  toolbox; stage files by explicit path.
- Push only when the user asks. Merge locally (`git merge --no-ff`) unless they ask for a PR.
- Test results are reported exactly as run: the command, pass/fail/skip counts, and the failing
  output. Unavailable infrastructure (no Postgres, no Docker, no Redis) is reported as **skipped
  — not run**, never as passing.
- Secrets never appear in the report: cite `file:line` and the kind of secret, not its value.

## Phases

### Phase 1 — Inspect

Run `git branch --show-current` and `git status --porcelain`. Uncommitted or untracked changes →
stop and ask (gate above).

Inventory the repository: `git ls-files` (count and group by folder), `pyproject.toml`, `uv.lock`
versions of `fastmcp`, `mcp`, `pydantic`, `sqlalchemy`, `asyncpg`, `boto3`, `httpx`, `redis`,
`sqlglot`, `Dockerfile`, `.github/workflows/*.yml`, `.env.example`, `README.md`, `AGENTS.md`,
`CLAUDE.md`.

**Done when** you have the file count, the dependency versions, and the list of every Python
module under `src/opsinsight/` grouped by layer.

### Phase 2 — Trace the request lifecycle

Read, in order, and write down the path one tool call takes:
`server.py` (`run`, `main`, `main_stdio`) → `core/transport_security.py` (bearer auth, Host/Origin)
→ `core/app.py` (shared `mcp`, middleware registration) → `core/audit.py` → `core/rate_limit.py`
→ one tool file per source → its service method → guardrail → client/engine → redaction →
`BaseService.ok` / `ToolError.to_response()`. Do the same for one resource read and one prompt fetch.

**Done when** you can state, with file references, where auth, rate limiting, audit, validation,
execution, redaction and error translation each happen, for HTTP and for stdio.

### Phase 3 — Review every module

Read `CHECKLIST.md`. Then read every non-test module under `src/opsinsight/` in full, plus the
test file beside each module you raise a finding on. Track coverage as you go: a module counts as
reviewed only once you have read it in full.

List the registered inventory and compare it against the pins:
`EXPECTED_TOOLS` / `EXPECTED_PROMPTS` in `core/audit_test.py`, the package `__init__.py` imports,
and `docs/tool-reference.md`.

**Done when** every module is marked reviewed (or excluded with a reason) and every checklist area
has either findings or an explicit "no issues found" with the files checked.

### Phase 4 — Security and performance pass

Run the security and performance sections of `CHECKLIST.md` across the whole tree, not only the
files that drew findings in Phase 3. Severity: CRITICAL / HIGH / MEDIUM / LOW / INFORMATIONAL.
A finding needs a concrete failure scenario (input or state → wrong result); without one it is
INFORMATIONAL at most.

### Phase 5 — Findings report, then stop

Run the baseline validations (section "Validation commands" below) so the report shows the state
*before* any change. Write the report from `REPORT.md` with Approval Status all "pending". For
each finding give root cause, affected modules, side effects of the fix, and the recommended fix.

Ask the user which findings to fix (multi-select by finding ID, plus "none — review only").
Anything that changes a public tool name, input schema, output envelope or transport behavior is
listed separately and needs its own explicit yes.

**Stop here until the user answers.**

### Phase 6–7 — Implement approved fixes

Create the branch from up-to-date local `main` (check it doesn't already exist). Fix only the
approved findings, following the repo conventions in `AGENTS.md` "Common tasks". Each fix comes
with a test that goes red without it. Leave unrelated modules alone; add no dependency without
asking.

### Phase 8 — Validate

Run every validation command. A failure is fixed in the code (or the test, if the test is wrong —
say why), never by skipping, deleting or loosening a test, lowering `fail_under`, or editing
`ci.yml`.

**Done when** lint is clean, unit tests pass, coverage ≥ 85%, and every non-run check is listed
with its reason.

### Phase 9 — Diff review

Read `git diff main...HEAD` and `git status` in full. Check against `CHECKLIST.md` "Pre-commit".

### Phase 10–11 — Final report and Approval 1

Update the report: modified files, test results after the change, remaining issues. Show it, then
ask **Approval 1: commit?** naming the branch and the exact files to stage. **Stop.**

### Phase 12 — Commit

After an explicit yes: `git add <path> ...` for the listed files only, `git diff --cached --stat`
and `git diff --cached` to confirm the staged set matches, then commit with a conventional message
(`fix: ...`, `feat: ...`) ending in the attribution line the session requires.

### Phase 13–14 — Merge readiness and Approval 2

`git log main..HEAD`, `git diff main...HEAD --stat`, check `main` hasn't moved
(`git merge-base --is-ancestor main HEAD`; if it has, test-merge with
`git merge --no-commit --no-ff main` on the branch and abort after), re-run lint + unit tests on the
final branch state. Write the merge-readiness section and ask **Approval 2: merge into main?**
**Stop.**

### Phase 15 — Merge

After an explicit yes: `git switch main && git merge --no-ff <branch>`, run unit tests once more on
`main`, report the merge commit hash. Leave the branch in place and don't push unless asked.

## Validation commands

Taken from `AGENTS.md` and `.github/workflows/ci.yml`; use these, not defaults.

| Check | Command | Notes |
|---|---|---|
| Lint | `uv run ruff check .` | CI gate, must be clean |
| Format | `uv run ruff format --check .` | Not a CI gate: report drift as INFORMATIONAL, don't reformat unrelated files |
| Unit + coverage | `uv run pytest -m "not integration" -q --cov=opsinsight --cov-report=term` | CI gate, `fail_under = 85` |
| Integration | `uv run pytest -m integration -q` | Auto-skips without Postgres; report skips as not run |
| Type check | — | No mypy/pyright is configured. Report "not configured"; adding one needs approval |
| Startup (HTTP) | `uv run investigation-server` in the background, confirm it listens on `:8001/mcp`, then stop it | Needs no external services to start |
| Startup (stdio) | `uv run python -c "import opsinsight.server"` | Confirms import-time registration and no circular import |
| Doctor | `uv run opsinsight-mcp doctor` | Connectivity report; failures without creds are expected — report, don't fix |
| Docker | `docker build -t investigation-mcp .` | Only if `docker` is available; else skipped |
| Dependencies | `uv lock --check`, `uv tree --outdated` (if supported) | Report drift; dependency bumps are out of scope unless approved |
