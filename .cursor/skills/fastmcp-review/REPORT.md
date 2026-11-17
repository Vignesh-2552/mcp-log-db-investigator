# Report template

Fill every section. Write "not run — <reason>" or "none found (files checked: ...)" rather than
leaving a section blank or implying coverage you don't have.

```markdown
# FASTMCP BACKEND CODE REVIEW REPORT

## A. Project Architecture
- Structure: <layers and package layout, one line each>
- Python: <requires-python / CI version>
- FastMCP / MCP SDK: <versions from uv.lock>
- Key dependencies: <name version, ...>
- Server architecture: <request lifecycle from Phase 2, with file refs>
- MCP transport: <HTTP path/port, stdio entry point>
- External integrations: <Postgres, CloudWatch, New Relic, Redis — configured / optional>

## B. Review Coverage
- Total tracked files: <n> (Python: <n>, tests: <n>)
- Modules reviewed in full: <n> / <n> — <list or grouped list>
- Excluded: <file/glob — reason>

## C. Findings

| ID | Severity | Category | File | Problem | Recommended Fix |
|---|---|---|---|---|---|
| F1 | HIGH | security | `src/opsinsight/...:<line>` | <problem + failure scenario> | <fix> |

Per finding (below the table): root cause, affected modules, side effects of the fix.
Contract-changing fixes (tool name, input schema, output envelope, transport): listed separately.

## D. MCP Review
- Server initialization:
- Transport:
- Tools (<n> registered, matches EXPECTED_TOOLS: yes/no):
- Resources:
- Prompts (<n>, matches EXPECTED_PROMPTS: yes/no):
- Authentication / authorization:
- Tool execution (validation, errors, timeouts):

## E. Testing Results
| Check | Command | Result |
|---|---|---|
| Ruff lint | `uv run ruff check .` | |
| Ruff format | `uv run ruff format --check .` | |
| Unit tests | `uv run pytest -m "not integration" ...` | <passed/failed/skipped counts> |
| Coverage | (same run) | <total %> vs 85% |
| Integration | `uv run pytest -m integration -q` | |
| Type check | — | not configured |
| Startup HTTP / stdio | | |
| Docker build | | |
| Dependencies | | |

Skipped / unavailable infrastructure: <list>

## F. Git Review
- Current branch:
- Target branch: main
- Working branch:
- Modified files:
- Staged files:
- Commit: <hash + subject, or "not committed">

## G. Approval Status
- Technical approval (fixes to implement): pending / approved: <IDs>
- Commit approval (Approval 1): pending / approved
- Merge approval (Approval 2): pending / approved

## H. Final Recommendation
BLOCKED | FIXES REQUIRED | APPROVED FOR COMMIT | APPROVED FOR MERGE — <one-line reason>
```

## Merge-readiness section (Phase 13)

Append to the report:

- Commits on branch: `git log --oneline main..HEAD`
- Files changed vs main: `git diff --stat main...HEAD`
- `main` moved since branching: yes/no; conflicts: none / <files>
- Regression run on final branch state: <commands + counts>
- Recommendation: ready to merge / not ready — <reason>
