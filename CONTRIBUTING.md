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

## Releasing

Releases publish to PyPI from `.github/workflows/publish.yml` when a `v*` tag
is pushed. A version number can be published only once, so check before tagging.

1. In a PR: bump `version` in `pyproject.toml`, run `uv lock`, and add a
   `CHANGELOG.md` section for the new version.
2. Merge the PR into `main` (direct pushes to `main` are blocked).
3. Tag the merge commit and push the tag (one line at a time in Windows PowerShell 5.1):
   ```
   git checkout main
   git pull --ff-only origin main
   git tag -a vX.Y.Z -m "opsinsight-mcp X.Y.Z"
   git push origin vX.Y.Z
   ```
   The workflow fails before publishing if the tag does not match the
   `pyproject.toml` version.
4. Check the run in the Actions tab, then verify a fresh install:
   `uvx --refresh opsinsight-mcp@X.Y.Z` should start and list its tools.
