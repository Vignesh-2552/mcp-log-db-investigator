# Plan: Move All Test Files Into `src/opsinsight/tests/`

**Status:** ⏳ Awaiting Approval  
**Author:** AI Agent  
**Date:** 2026-11-17  
**Branch:** `refactor/move-tests-to-tests-folder`

---

## 1. Goal

Restructure the project so that every `*_test.py` file is moved from its current location
(co-located next to the source module) into a centralised `src/opsinsight/tests/` directory
tree that mirrors the source layout.

### Current convention (before)

```
src/opsinsight/
├── core/
│   ├── audit.py
│   ├── audit_test.py        ← test lives next to source
│   └── ...
├── service/
│   ├── database_service.py
│   ├── database_service_test.py
│   └── ...
└── ...
```

### Target convention (after)

```
src/opsinsight/
├── core/
│   ├── audit.py              ← source stays
│   └── ...
├── tests/                    ← NEW top-level test folder
│   ├── __init__.py
│   ├── conftest.py           ← moved from src/opsinsight/conftest.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── audit_test.py
│   │   ├── cache_test.py
│   │   ├── config_test.py
│   │   ├── container_test.py
│   │   ├── limits_test.py
│   │   ├── logging_config_test.py
│   │   ├── rate_limit_test.py
│   │   ├── redaction_test.py
│   │   ├── response_cache_test.py
│   │   ├── time_window_test.py
│   │   └── transport_security_test.py
│   ├── integrations/
│   │   ├── __init__.py
│   │   ├── cloudwatch/
│   │   │   ├── __init__.py
│   │   │   └── guardrail_test.py
│   │   ├── database/
│   │   │   ├── __init__.py
│   │   │   ├── engine_test.py
│   │   │   ├── guardrail_test.py
│   │   │   ├── introspect_test.py
│   │   │   └── queries_test.py
│   │   ├── newrelic/
│   │   │   ├── __init__.py
│   │   │   ├── client_test.py
│   │   │   └── guardrail_test.py
│   │   ├── redis/
│   │   │   ├── __init__.py
│   │   │   └── client_test.py
│   │   └── tickets/
│   │       ├── __init__.py
│   │       ├── adf_test.py
│   │       ├── guardrail_test.py
│   │       ├── jira_client_test.py
│   │       └── linear_client_test.py
│   ├── prompts/
│   │   ├── __init__.py
│   │   ├── common_test.py
│   │   ├── investigate_incident_test.py
│   │   ├── slow_endpoint_rca_test.py
│   │   └── trace_user_journey_test.py
│   ├── resources/
│   │   ├── __init__.py
│   │   └── schema_test.py
│   ├── service/
│   │   ├── __init__.py
│   │   ├── cloudwatch_logs_service_cache_test.py
│   │   ├── cloudwatch_logs_service_inventory_test.py
│   │   ├── cloudwatch_logs_service_test.py
│   │   ├── cloudwatch_metrics_service_test.py
│   │   ├── database_service_integration_test.py
│   │   ├── database_service_limits_test.py
│   │   ├── database_service_test.py
│   │   ├── newrelic_service_test.py
│   │   ├── ticket_service_test.py
│   │   └── trace_service_test.py
│   ├── tools/
│   │   ├── __init__.py
│   │   └── tool_wiring_test.py
│   ├── doctor_test.py
│   ├── release_version_test.py
│   └── server_test.py
└── ...
```

---

## 2. Files to Move (42 test files + 1 conftest)

| # | Current Location | New Location |
|---|---|---|
| 1 | `src/opsinsight/conftest.py` | `src/opsinsight/tests/conftest.py` |
| 2 | `src/opsinsight/doctor_test.py` | `src/opsinsight/tests/doctor_test.py` |
| 3 | `src/opsinsight/release_version_test.py` | `src/opsinsight/tests/release_version_test.py` |
| 4 | `src/opsinsight/server_test.py` | `src/opsinsight/tests/server_test.py` |
| 5 | `core/audit_test.py` | `tests/core/audit_test.py` |
| 6 | `core/cache_test.py` | `tests/core/cache_test.py` |
| 7 | `core/config_test.py` | `tests/core/config_test.py` |
| 8 | `core/container_test.py` | `tests/core/container_test.py` |
| 9 | `core/limits_test.py` | `tests/core/limits_test.py` |
| 10 | `core/logging_config_test.py` | `tests/core/logging_config_test.py` |
| 11 | `core/rate_limit_test.py` | `tests/core/rate_limit_test.py` |
| 12 | `core/redaction_test.py` | `tests/core/redaction_test.py` |
| 13 | `core/response_cache_test.py` | `tests/core/response_cache_test.py` |
| 14 | `core/time_window_test.py` | `tests/core/time_window_test.py` |
| 15 | `core/transport_security_test.py` | `tests/core/transport_security_test.py` |
| 16 | `integrations/cloudwatch/guardrail_test.py` | `tests/integrations/cloudwatch/guardrail_test.py` |
| 17 | `integrations/database/engine_test.py` | `tests/integrations/database/engine_test.py` |
| 18 | `integrations/database/guardrail_test.py` | `tests/integrations/database/guardrail_test.py` |
| 19 | `integrations/database/introspect_test.py` | `tests/integrations/database/introspect_test.py` |
| 20 | `integrations/database/queries_test.py` | `tests/integrations/database/queries_test.py` |
| 21 | `integrations/newrelic/client_test.py` | `tests/integrations/newrelic/client_test.py` |
| 22 | `integrations/newrelic/guardrail_test.py` | `tests/integrations/newrelic/guardrail_test.py` |
| 23 | `integrations/redis/client_test.py` | `tests/integrations/redis/client_test.py` |
| 24 | `integrations/tickets/adf_test.py` | `tests/integrations/tickets/adf_test.py` |
| 25 | `integrations/tickets/guardrail_test.py` | `tests/integrations/tickets/guardrail_test.py` |
| 26 | `integrations/tickets/jira_client_test.py` | `tests/integrations/tickets/jira_client_test.py` |
| 27 | `integrations/tickets/linear_client_test.py` | `tests/integrations/tickets/linear_client_test.py` |
| 28 | `prompts/common_test.py` | `tests/prompts/common_test.py` |
| 29 | `prompts/investigate_incident_test.py` | `tests/prompts/investigate_incident_test.py` |
| 30 | `prompts/slow_endpoint_rca_test.py` | `tests/prompts/slow_endpoint_rca_test.py` |
| 31 | `prompts/trace_user_journey_test.py` | `tests/prompts/trace_user_journey_test.py` |
| 32 | `resources/schema_test.py` | `tests/resources/schema_test.py` |
| 33 | `service/cloudwatch_logs_service_cache_test.py` | `tests/service/cloudwatch_logs_service_cache_test.py` |
| 34 | `service/cloudwatch_logs_service_inventory_test.py` | `tests/service/cloudwatch_logs_service_inventory_test.py` |
| 35 | `service/cloudwatch_logs_service_test.py` | `tests/service/cloudwatch_logs_service_test.py` |
| 36 | `service/cloudwatch_metrics_service_test.py` | `tests/service/cloudwatch_metrics_service_test.py` |
| 37 | `service/database_service_integration_test.py` | `tests/service/database_service_integration_test.py` |
| 38 | `service/database_service_limits_test.py` | `tests/service/database_service_limits_test.py` |
| 39 | `service/database_service_test.py` | `tests/service/database_service_test.py` |
| 40 | `service/newrelic_service_test.py` | `tests/service/newrelic_service_test.py` |
| 41 | `service/ticket_service_test.py` | `tests/service/ticket_service_test.py` |
| 42 | `service/trace_service_test.py` | `tests/service/trace_service_test.py` |
| 43 | `tools/tool_wiring_test.py` | `tests/tools/tool_wiring_test.py` |

> All paths above are relative to `src/opsinsight/`.

---

## 3. New Directories & Files to Create

Create empty `__init__.py` in each new test subdirectory so pytest discovers them as packages:

```
src/opsinsight/tests/__init__.py
src/opsinsight/tests/core/__init__.py
src/opsinsight/tests/integrations/__init__.py
src/opsinsight/tests/integrations/cloudwatch/__init__.py
src/opsinsight/tests/integrations/database/__init__.py
src/opsinsight/tests/integrations/newrelic/__init__.py
src/opsinsight/tests/integrations/redis/__init__.py
src/opsinsight/tests/integrations/tickets/__init__.py
src/opsinsight/tests/prompts/__init__.py
src/opsinsight/tests/resources/__init__.py
src/opsinsight/tests/service/__init__.py
src/opsinsight/tests/tools/__init__.py
```

---

## 4. Configuration Changes

### 4.1 `pyproject.toml` — pytest section

**Before:**
```toml
[tool.pytest.ini_options]
# Each test file sits next to the module it tests: foo.py -> foo_test.py.
testpaths = ["src"]
python_files = ["*_test.py"]
```

**After:**
```toml
[tool.pytest.ini_options]
# Tests are centralised under src/opsinsight/tests/.
testpaths = ["src/opsinsight/tests"]
python_files = ["*_test.py"]
```

### 4.2 `pyproject.toml` — coverage (if `source` is set)

Verify the `[tool.coverage.run]` section still covers `src/opsinsight`. No change expected
since tests import the production package by its absolute name.

---

## 5. Import Adjustments

**No changes expected.** All existing test files use absolute imports like:

```python
from opsinsight.core.config import Settings
from opsinsight.service.database_service import DatabaseService
```

These absolute imports work regardless of where the test file lives, as long as `src/` (or
the installed package) is on `sys.path` — which `uv run pytest` ensures.

> [!IMPORTANT]
> If any test file contains **relative imports** (e.g., `from .guardrail import ...`), those
> imports must be rewritten to absolute form. A grep for `from \.` will catch these during
> implementation.

---

## 6. Documentation Updates

The following documents reference the "tests live beside the code" convention and must be
updated to reflect the new layout:

| Document | Section to Update |
|---|---|
| `AGENTS.md` | "Testing conventions" — change "Tests live beside the code" to "Tests live under `src/opsinsight/tests/`" |
| `CLAUDE.md` | Any reference to `foo.py → foo_test.py` co-location |
| `docs/code-structure.md` | File map — add `tests/` tree, remove `*_test.py` from source directories |
| `pyproject.toml` | Comment above `testpaths` |

---

## 7. Step-by-Step Execution Plan

| Step | Action | Verification |
|------|--------|-------------|
| 1 | Create branch `refactor/move-tests-to-tests-folder` | `git branch` |
| 2 | Create `src/opsinsight/tests/` and all sub-directories with `__init__.py` | Directories exist |
| 3 | Move `conftest.py` to `src/opsinsight/tests/conftest.py` | File moved |
| 4 | Move all 42 `*_test.py` files to their mirror locations under `tests/` | All files in new locations |
| 5 | Delete the original `*_test.py` files from source directories | No `*_test.py` in source dirs |
| 6 | Grep for relative imports (`from \.`) and fix any found | No relative imports in tests |
| 7 | Update `testpaths` in `pyproject.toml` | Config points to new path |
| 8 | Run `uv run ruff check .` | Clean (exit 0) |
| 9 | Run `uv run pytest -m "not integration" -q` | All tests pass |
| 10 | Run `uv run pytest -m "not integration" --cov=opsinsight` | Coverage ≥ 85% |
| 11 | Update `AGENTS.md`, `CLAUDE.md`, `docs/code-structure.md` | Docs reflect new layout |
| 12 | Commit with message `refactor: move test files to src/opsinsight/tests/` | Clean commit |

---

## 8. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| `conftest.py` fixtures not discovered by pytest from new location | Tests fail to find `settings_override`, `pg_available` | pytest discovers `conftest.py` files in `testpaths` and parent dirs. Moving it into `tests/` keeps it discoverable. Verify in Step 9. |
| Relative imports break after move | ImportError in affected test files | Step 6 greps and rewrites any relative imports to absolute. |
| Coverage drop because `testpaths` change | CI fails on coverage gate | `--cov=opsinsight` measures the production package, not test location. Verify in Step 10. |
| Broken references in IDE / editor configs | Dev experience impact | Update `.vscode/settings.json` or similar if present. |
| Existing PRs conflict | Merge conflicts in moved files | Coordinate merge timing; this is a one-time structural change. |

---

## 9. Rollback

If tests fail after the move and cannot be fixed quickly:

1. `git checkout main -- src/opsinsight/` to restore all files.
2. Revert the `pyproject.toml` change.
3. Run tests to confirm green.

---

> [!NOTE]
> This plan moves **only test files**. No production source code is modified.
> All changes are mechanical (file moves + config update + doc updates).

**Awaiting your approval to proceed with implementation.**
