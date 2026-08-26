---
name: extract-source-files
description: Extract inline SQL/NRQL/CloudWatch query strings, magic constants, and ad-hoc dict-shaped return values in this repo into per-source queries.py/constants.py/models.py files, using typed dataclasses (DictableMixin) for response shapes. Use when new code has crept back into integrations/<source>/*.py or service/<source>_service.py as inline strings/literals/dict literals, or when the user asks to "clean up"/"extract"/"restructure" a source's queries or constants.
---

# Extract queries / constants / models

This codebase keeps three concerns out of `integrations/<source>/{guardrail,client,engine,
introspect}.py` and `service/<source>_service.py`: raw query text, magic constants, and ad-hoc
response-shape dicts. Each source (`database`, `cloudwatch`, `newrelic`) has its own
`integrations/<source>/{queries.py,constants.py,models.py}` for these. This skill is the checklist
for moving newly-added inline code into that structure — it is a **pure extraction**: never change
a tool's JSON response shape or the `tools → service → integrations` layering while doing this.

## What goes where

| Found in the wild | Goes in |
|---|---|
| A literal SQL/NRQL/Insights query string, or a `f"SELECT ... {x}"` built from runtime values | `queries.py` — either a plain string constant, or a `build_x(...)` template function if it needs runtime values |
| A regex, blocklist, numeric cap, tuple of allowed values | `constants.py` |
| A function/method building a literal `dict[str, Any]` return value (not a dynamically-keyed working structure) | `models.py` — a `@dataclass` inheriting `core.models.DictableMixin` |

**Not extraction candidates:** dynamically-keyed internal accumulators (e.g. CloudWatch's
`describe_log_fields` per-cluster `dict[frozenset, dict]` scratch structure), raw third-party API
response passthroughs where the keys are the external contract (e.g. boto3's `Datapoints` dicts
with `Timestamp`/`Average`/... — modeling these per-field would rename response keys, a real shape
change, not a refactor), and anything whose field names are inherently dynamic (e.g. Logs Insights
result rows keyed by the query's own field list).

## The one hard rule: don't cross a locked boundary

Before changing any function's *return type* from `dict`/`str` to a dataclass, check whether that
return value is:
1. **Faked directly by an existing unit test** doing dict/string-specific operations on it
   (`result["status"]`, `result.upper()`, etc.), or
2. **A constructor-injected DI seam's contract** — e.g. `NewRelicService.__init__`'s `run_nrql_fn`,
   `CloudWatchService.__init__`'s `logs_client_factory`/`metrics_client_factory` — whose test fakes
   return a fixed dict shape.

If either is true, the function's **public return type must not change**. You may still build the
dataclass internally for typed construction, then flatten it (`.to_dict()`) or drop it entirely
before it crosses that boundary. Three existing examples of this: `cloudwatch/guardrail.py`'s
`poll_query_with_backoff` (generic `Callable[[], dict] -> dict`, tested with hand-rolled partial
dicts), `newrelic/client.py`'s `run_nrql` (DI seam, stays `-> dict`), `newrelic/guardrail.py`'s
`validate_nrql` (stays `-> str`, ~10 direct string assertions in its test file). Grep the relevant
`tests/unit/test_*.py` file before touching any function's signature to confirm which bucket it's
in.

## Database's specific gotcha: parameterized SQL vs. sqlglot re-serialization

If a `queries.py` builder produces SQL with named bind params (`:identifier`, `:pattern`, etc.)
that then gets re-validated through `guardrail.validate_sql(...)`, **never execute
`validated.sql`** — only use `validated.limit`. sqlglot's postgres-dialect re-serialization rewrites
`:name` into pyformat markers (`%(name)s`), which SQLAlchemy's `text()` can't bind, silently
breaking the query at runtime (caught the hard way once already — see
`introspect.py`'s `search_by_identifier`/`resolve_store` `_search_one` closures for the working
pattern: keep the original template string, append `LIMIT {validated.limit}` as a literal).

## Building the dataclasses

```python
# integrations/<source>/models.py
from dataclasses import dataclass
from typing import Any
from core.models import DictableMixin

@dataclass
class MyResult(DictableMixin):
    field_one: str
    field_two: list[NestedThing]   # nested dataclasses work — asdict() recurses automatically
```

- Every dataclass inherits `DictableMixin` (`core/models.py`) for `.to_dict()`.
- Give **structurally different** shapes their own class even if they look similar — e.g. this
  codebase's `IdentifierSkip` (has `source_type`) and `StoreSkip` (doesn't) are deliberately
  separate, not one class with an `Optional[str] = None` field, because `asdict()` would inject a
  spurious `"source_type": null` key into responses that never had that field before.
- Call `.to_dict()` exactly once, in the service method, right before `self.ok(...)` — not deeper
  in the stack.

## Verify

```powershell
uv run ruff check .              # catches leftover unused old constants/imports
uv run pytest tests/unit -q      # must stay 100% green, unmodified
uv run pytest tests/integration -m integration   # if database was touched; requires live Postgres
```

If a test breaks, that's a signal a function's return type crossed a boundary it shouldn't have —
revert that one function to construct-then-flatten rather than editing the test.

After extraction, spot-check for orphaned code: grep the old private (underscore-prefixed) name
across the whole repo to confirm the old definition was deleted, not left duplicated alongside the
new public one in `queries.py`/`constants.py`.
