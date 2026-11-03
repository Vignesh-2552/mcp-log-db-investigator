# Implementation Plan: Interactive MCP Prompts (Native Slash Commands)

**Date:** 2026-11-03  
**Target Release:** `opsinsight-mcp` v0.4.0 (Phase 2: Guided Investigation)  
**Git Branch:** `feat/mcp-prompts`  
**Status:** Planning / In Progress  
**Document Location:** [`docs/plan/mcp-prompts-plan.md`](file:///d:/Vignesh/mcp_tool/docs/plan/mcp-prompts-plan.md)  

---

## 1. Executive Summary & Objective

In the current version of `opsinsight-mcp`, the server exposes 17 read-only query tools and 3 resources. However, when an AI model (such as Claude Desktop or Cursor) is asked to investigate an issue, it must manually remember or be instructed how to sequence queries ("discover before you query"). Without the external Agensi skill installed, models frequently make zero-row queries or guess schema names.

**Interactive MCP Prompts** are native capabilities defined in the Model Context Protocol (MCP) and supported by FastMCP (`@mcp.prompt()`). They surface as interactive slash commands inside compatible AI clients (e.g., typing `/investigate_incident` in Claude Desktop).

### Objectives:
1. **Zero-Configuration Workflows:** Move the proven "discover-then-query" workflow from external skills directly into the server as native MCP slash commands.
2. **Standardized Incident Investigations:** Give AI models structured, step-by-step instructions with grounded query templates for support tickets, latency spikes, and user journeys.
3. **Guardrail-Aware Execution:** Encode guardrail error recovery rules, PII awareness, and time-window clamping instructions directly in the prompt templates.

---

## 2. Prompts Specification

Three native prompts will be implemented in accordance with [`docs/design.md`](file:///d:/Vignesh/mcp_tool/docs/design.md) §4.5 and [`docs/roadmap.md`](file:///d:/Vignesh/mcp_tool/docs/roadmap.md):

```
┌────────────────────────────────────────────────────────────────────────┐
│                        FastMCP Prompts Layer                           │
├──────────────────────────┬─────────────────────────┬───────────────────┤
│  /investigate_incident   │   /trace_user_journey   │ slow_endpoint_rca │
│  (Support Tickets & RCA) │   (Entity Tracking)     │ (APM & DB Bottl.) │
└──────────────────────────┴─────────────────────────┴───────────────────┘
```

---

### A. Prompt 1: `investigate_incident`

*End-to-end incident root cause analysis for customer support tickets.*

- **Name:** `investigate_incident`
- **Description:** Guided workflow for investigating customer support tickets across PostgreSQL, CloudWatch, and New Relic.
- **Parameters:**
  - `ticket_text` (str, required): The raw support ticket, customer issue description, or error log.
  - `time_window` (str, optional, default: `"24h"`): Estimated time window (e.g. `"2h"`, `"24h"`, `"2026-10-28T12:00:00Z/2026-10-28T16:00:00Z"`).
  - `store_or_domain` (str, optional): Store name, hostname, or tenant slug if known.

#### Injected Workflow Steps:
1. **Identifier Extraction & Window Pinning:** Extract all candidate keys (`order_id`, `customer_id`, `request_id`, error codes) and pin down the narrowest possible time range.
2. **Identifier Resolution:** Use `db_resolve_store` for domains/store names and `db_search_by_identifier` to locate relevant database tables without guessing.
3. **Database Grounding:** Inspect schema via `db_describe_table`, retrieve state via `db_run_query`, and extract trace correlation keys (`request_id`, `trace_id`).
4. **Cross-Source Telemetry:** Call `trace_request` or `cw_get_trace_events` / `nr_run_nrql_query` to pull corresponding application logs and APM spans.
5. **Pattern & Metric Correlation:** Run cookbook queries for top error patterns and check `cw_get_metric_stats` for infrastructure anomalies.
6. **Synthesized Report Output:** Format findings into a structured incident report containing:
   - Summary of events and timeline
   - Verified facts vs unverified hypotheses
   - Root cause determination
   - Recommended resolution and customer-facing response draft

---

### B. Prompt 2: `trace_user_journey`

*Follow a single transaction or user action across the entire stack.*

- **Name:** `trace_user_journey`
- **Description:** Trace an entity (Order ID, User ID, Request UUID) across database states, CloudWatch logs, and New Relic APM.
- **Parameters:**
  - `identifier` (str, required): The unique identifier to trace (e.g. `ord_89214`, `req-99a8b7`, `user_342`).
  - `id_type` (str, optional, default: `"auto"`): Hint on identifier type (`"order_id"`, `"user_id"`, `"request_id"`).
  - `time_window` (str, optional, default: `"24h"`): Time window for the trace.

#### Injected Workflow Steps:
1. **Fan-Out Search:** Invoke `trace_request` to query PostgreSQL, CloudWatch, and New Relic simultaneously for the identifier.
2. **Timeline Reconstruction:** Chronologically order database row modifications, gateway request logs, backend microservice events, and third-party webhook calls.
3. **Anomaly Identification:** Detect unexpected status jumps, dropped requests, retry loops, or timeout errors.
4. **Output Matrix:** Render an entity journey map showing:
   - Timestamp | Service/Source | Action/State | Status | Details

---

### C. Prompt 3: `slow_endpoint_rca`

*Investigate latency degradation, timeout spikes, and performance bottlenecks.*

- **Name:** `slow_endpoint_rca`
- **Description:** Diagnose performance bottlenecks and high latency for an API route or transaction.
- **Parameters:**
  - `endpoint_or_transaction` (str, required): The HTTP path or New Relic transaction name (e.g., `/api/checkout`, `WebTransaction/Expressjs/POST//orders`).
  - `time_window` (str, optional, default: `"4h"`): Time window when latency was elevated.
  - `threshold_ms` (int, optional, default: `1000`): SLA or latency threshold in milliseconds.

#### Injected Workflow Steps:
1. **APM Latency Breakdown:** Use `nr_run_nrql_query` to fetch percentiles (`p50`, `p95`, `p99`), duration breakdown (database time vs external service vs application CPU), and error rate over the window.
2. **Slow Query Inspection:** Identify slow database queries associated with the endpoint. Run `db_explain_query` on complex queries to check for sequential scans or missing indexes.
3. **Infrastructure Saturation Check:** Call `cw_get_metric_stats` for RDS CPU utilization, freeable memory, connection count, and Lambda/ECS throttling.
4. **Actionable Recommendations:** Provide concrete remedies (e.g. missing composite index, unindexed `WHERE` clause, slow external API dependency, or connection pool exhaustion).

---

## 3. Architecture & Code Structure

In accordance with [`docs/code-structure.md`](file:///d:/Vignesh/mcp_tool/docs/code-structure.md), prompt modules will be placed in a dedicated, clean package:

```
src/opsinsight/
├── prompts/
│   ├── __init__.py                 # Exports & auto-registers prompts onto FastMCP app
│   ├── investigate_incident.py     # Prompt definition: /investigate_incident
│   ├── trace_user_journey.py       # Prompt definition: /trace_user_journey
│   ├── slow_endpoint_rca.py        # Prompt definition: /slow_endpoint_rca
│   └── prompts_test.py             # Unit tests for prompt rendering & parameter schema
├── server.py                       # Imports `opsinsight.prompts` during server startup
└── core/app.py                     # Single shared FastMCP instance (`mcp`)
```

### Prompt Registration Pattern:
```python
from opsinsight.core.app import mcp

@mcp.prompt(name="investigate_incident", description="...")
def investigate_incident(ticket_text: str, time_window: str = "24h", store_or_domain: str | None = None) -> str:
    ...
```

---

## 4. Step-by-Step Task Breakdown

### Phase 1: Package Foundation & Infrastructure
- [ ] Create directory [`src/opsinsight/prompts/`](file:///d:/Vignesh/mcp_tool/src/opsinsight/prompts/).
- [ ] Create `__init__.py` registering prompt modules onto `mcp`.
- [ ] Update [`src/opsinsight/server.py`](file:///d:/Vignesh/mcp_tool/src/opsinsight/server.py) to import `opsinsight.prompts` along with tools and resources.

### Phase 2: Prompt Implementations
- [ ] Implement `src/opsinsight/prompts/investigate_incident.py`:
  - Build comprehensive system instruction template encoding discovery rules, safety caps, and cookbook references.
- [ ] Implement `src/opsinsight/prompts/trace_user_journey.py`:
  - Build journey-tracing template with `trace_request` guidance and timeline synthesis instructions.
- [ ] Implement `src/opsinsight/prompts/slow_endpoint_rca.py`:
  - Build performance diagnostic template with APM percentiles, `db_explain_query` guidance, and metric checks.

### Phase 3: Unit Testing Suite
- [ ] Create `src/opsinsight/prompts/prompts_test.py`:
  - Test that all 3 prompts are registered in `mcp`.
  - Test parameter validation (defaults and custom inputs).
  - Verify prompt content contains required tool references, guardrail guidance, and expected output formatting.
  - Ensure zero network or external dependencies during tests.

### Phase 4: Quality & Documentation Gate
- [ ] Run `ruff check .` to verify clean code formatting.
- [ ] Run `pytest -m "not integration"` to ensure all unit tests pass and code coverage remains $\ge 85\%$.
- [ ] Update [`docs/tool-reference.md`](file:///d:/Vignesh/mcp_tool/docs/tool-reference.md) with a new section documenting the 3 MCP Prompts.
- [ ] Update [`README.md`](file:///d:/Vignesh/mcp_tool/README.md) and [`CHANGELOG.md`](file:///d:/Vignesh/mcp_tool/CHANGELOG.md).

---

## 5. Acceptance Criteria

1. **Client Compatibility:** Claude Desktop, Cursor, and other MCP clients list `/investigate_incident`, `/trace_user_journey`, and `/slow_endpoint_rca` in their prompt autocomplete.
2. **Pure & Fast:** Prompt generation is instant (pure string formatting) and performs no I/O at prompt-fetch time.
3. **Safety & Guardrails:** Prompt instructions explicitly forbid AI models from attempting mutations or requesting unredacted PII.
4. **CI Compliance:** Linting passes with 0 errors and test coverage exceeds 85%.

