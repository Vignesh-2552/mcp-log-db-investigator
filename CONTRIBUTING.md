# Contributing

Thanks for considering a contribution to the Investigation MCP Server.

## Before you start

This is a **read-only, guardrail-first** MCP server — every change should
preserve that property. If a change would let a tool write to the database,
bypass a guardrail, or return unredacted data, it needs an explicit
discussion first (open an issue), not just a PR.

## Setup

```powershell
uv sync
copy .env.example .env
```

You don't need every backend configured to contribute — Postgres, CloudWatch,
and New Relic are independent (see the README's
[What each source needs](README.md#what-each-source-needs)). Unit tests don't
require any of them; only `tests/integration/` needs a reachable Postgres,
and it auto-skips otherwise.

## Before opening a PR

```powershell
uv run ruff check .                              # lint — must be clean
uv run pytest tests/unit -q                      # must pass
uv run pytest tests/integration -m integration   # if you touched DB code and have Postgres available
```

CI (`.github/workflows/ci.yml`) runs the same checks on every push/PR — a red
check means the PR isn't mergeable as-is.

## Architecture and conventions

Read [`CLAUDE.md`](CLAUDE.md) first — it's the authoritative description of
this repo's layering (`tools/* → service/* → integrations/<source>/guardrail.py
→ client.py|engine.py`) and the conventions each layer follows. Highlights:

- **One file per tool** in `tools/<source>/`, named after the tool. The file
  is just the `@mcp.tool()` decorator, the docstring, and one call into the
  service layer — no logic there.
- **Guardrails are pure functions, no I/O.** A service method never executes
  a user-supplied query without passing it through the guardrail first.
- **A new tool file must be registered** in its source package's `__init__.py`
  (see `src/tools/database/__init__.py` etc.) or it silently never loads.
- **Errors are structured**, not bare rejections — every guardrail raises a
  `ToolError` subclass with a machine-readable `rule` and a `detail` string
  explaining what to change, so the calling model can self-correct.

For the step-by-step of adding a new tool, see
[`docs/tool-reference.md`](docs/tool-reference.md) → **How To Update A Tool**
and **Where To Add Tests**.

## Commit style

Recent history mostly follows [Conventional Commits](https://www.conventionalcommits.org/)
(`feat(scope): ...`, `fix(scope): ...`, `test: ...`, `docs: ...`) — please
follow the same pattern for new commits.

## What to add to the query cookbook

If a query pattern proves useful, or a source's field-naming quirk causes
repeated bad queries from the client model, add/update an entry in
[`src/data/query_cookbook.yaml`](src/data/query_cookbook.yaml) rather than
only fixing it in prose — see `CLAUDE.md` for why this matters more than it
looks like it should.
