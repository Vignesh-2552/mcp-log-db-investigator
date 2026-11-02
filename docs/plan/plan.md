# Implementation Plan: `opsinsight-mcp doctor` Diagnostic CLI

**Date:** 2026-11-02  
**Target Release:** `opsinsight-mcp` v0.3.0  
**Status:** Ready for Implementation  
**Location:** [`docs/plan/plan.md`](file:///d:/Vignesh/mcp_tool/docs/plan/plan.md)  

---

## 1. Overview & Objective

The **`opsinsight-mcp doctor`** command provides an instant, self-diagnostic pre-flight check for operators, developers, and automated deployment pipelines (CI/CD). It validates environment variables, network connectivity, authentication credentials, and safety guarantees (read-only enforcement, PII redaction) across all three observability pillars without booting the full MCP server.

### Key Objectives
1. **Fast Diagnostic Feedback:** Pinpoint broken credentials or network misconfigurations in seconds instead of debugging silent failures in LLM chat sessions.
2. **Strict Secret Masking:** Never leak passwords, bearer tokens, AWS secret keys, or New Relic API keys to the terminal or logs.
3. **CI/CD Readiness:** Return deterministic exit codes (`0` for success/skipped, `1` if any configured service fails).
4. **Zero Regressions:** Maintain 100% backward compatibility for stdio client launches (`uvx opsinsight-mcp`).

---

## 2. Specification of Diagnostic Checks

### A. PostgreSQL Check (`check_postgres`)
- **Connection Test:** Executes a lightweight query (`SELECT 1`) with an explicit connection timeout (default: 3 seconds) using `asyncpg`.
- **Read-Only Verification:** Runs `SHOW transaction_read_only` or executes within a read-only transaction block to guarantee write protection.
- **Latency & Version:** Measures round-trip execution latency in milliseconds and records PostgreSQL server version metadata.
- **Credential Sanitization:** If connection fails, strips raw credentials/passwords from the DSN in all error output.
- **Unconfigured Handling:** If `DB_URL` is default or unset, reports status as `SKIPPED (not configured)`.

### B. AWS CloudWatch Check (`check_cloudwatch`)
- **Credential Verification:** Checks for `CLOUDWATCH_ACCESS_KEY_ID`, `CLOUDWATCH_SECRET_ACCESS_KEY`, and `CLOUDWATCH_REGION`.
- **Log Group Allowlist Discovery:** If configured, calls `DescribeLogGroups` via `boto3` for the log groups in `CLOUDWATCH_ALLOWED_LOG_GROUP`.
- **Validation:** Confirms whether allowlisted log groups actually exist and are accessible.
- **Unconfigured Handling:** If AWS keys are omitted, reports `SKIPPED (not configured)`.

### C. New Relic Check (`check_newrelic`)
- **Credential Verification:** Checks for `NEW_RELIC_API_KEY` and `NEW_RELIC_ACCOUNT_ID`.
- **NerdGraph Ping:** Sends a lightweight GraphQL query (`query { actor { user { email } } }` or account query) via `httpx`.
- **Latency & Status:** Measures API response latency.
- **Unconfigured Handling:** If API key or Account ID is omitted, reports `SKIPPED (not configured)`.

### D. Server & Security Sanity Check (`check_server_security`)
- **Transport Security:** If `SERVER_HOST != 127.0.0.1` and `allow_unauthenticated_http` is `False`, verifies that `MCP_AUTH_TOKEN` is set.
- **PII Redaction:** Confirms `PII_REDACTION=true`.
- **Rate Limiting:** Confirms rate limiter parameters (`RATE_LIMIT_RPS`, `RATE_LIMIT_BURST`).

---

## 3. Secret Masking & Redaction Rules

Under no circumstances should raw secrets be printed to stdout/stderr:
- **Database DSN:** `postgresql+asyncpg://user:****@host:5432/dbname`
- **AWS Access Key:** Masked with first 4 and last 3 characters (e.g., `AKIA...4BA`).
- **AWS Secret Key:** Completely hidden as `[CONFIGURED]` or `[NOT SET]`.
- **New Relic API Key:** Masked with prefix (e.g., `NRAK-...`).
- **MCP Auth Token:** Completely hidden as `[CONFIGURED]` or `[NOT SET]`.

---

## 4. Output Formatting & Terminal UX

The output will use a clean, aligned text/table representation:

```text
opsinsight-mcp doctor (v0.2.0)
================================================================================
Service                Status    Latency    Details
--------------------------------------------------------------------------------
PostgreSQL             PASS      24ms       Connected (read-only verified, PostgreSQL 15.3)
AWS CloudWatch         PASS      112ms      1 log group accessible (corksy)
New Relic              PASS      185ms      NerdGraph API authenticated (Account: 7726086)
Server Security        PASS      -          Loopback-only, PII redaction active, Rate limit: 5.0 rps
================================================================================
Result: 4 passed, 0 warnings, 0 errors. All configured services healthy!
```

If a service is unconfigured:
```text
AWS CloudWatch         SKIPPED   -          Not configured (CLOUDWATCH_ACCESS_KEY_ID not set)
```

If a service fails:
```text
PostgreSQL             FAIL      -          ConnectionRefusedError: Host unreachable
--------------------------------------------------------------------------------
Result: 1 error encountered. Please check your configuration.
```

### Exit Codes:
- `0`: All configured checks passed (including skipped checks).
- `1`: One or more configured checks failed.

---

## 5. Architectural Design & File Structure

```
src/opsinsight/
├── doctor.py              # Diagnostic runner, check functions & table formatter
├── doctor_test.py         # 100% offline unit tests with mock clients
├── server.py              # CLI entry point routing (`doctor`, `--help`, `--version`)
docs/
└── plan/
    └── plan.md            # This planning document
```

### Dependency Inversion in `doctor.py`
To comply with repository guidelines (pure unit testing without real networks or databases):
- Check functions accept optional client/runner factories (matching protocols in [`service/ports.py`](file:///d:/Vignesh/mcp_tool/src/opsinsight/service/ports.py)).
- Unit tests pass mock responses into `check_postgres`, `check_cloudwatch`, and `check_newrelic`.

---

## 6. Step-by-Step Task Breakdown

### Phase 1: Core Diagnostic Logic (`src/opsinsight/doctor.py`)
- [ ] Define `CheckStatus` enum (`PASS`, `WARN`, `FAIL`, `SKIPPED`).
- [ ] Define `CheckResult` dataclass (`name`, `status`, `latency_ms`, `message`, `error`).
- [ ] Implement `mask_dsn(url: str) -> str` and `mask_key(key: str) -> str` secret masking utilities.
- [ ] Implement `check_postgres(settings, engine_factory=...) -> CheckResult`.
- [ ] Implement `check_cloudwatch(settings, client_factory=...) -> CheckResult`.
- [ ] Implement `check_newrelic(settings, http_client=...) -> CheckResult`.
- [ ] Implement `check_server_security(settings) -> CheckResult`.
- [ ] Implement `format_report(results: list[CheckResult]) -> str`.
- [ ] Implement `async def run_doctor(settings) -> int` (returns exit code).

### Phase 2: CLI Dispatch in `server.py`
- [ ] Parse CLI arguments in `main()` and `main_stdio()`.
- [ ] If `doctor` is provided in `sys.argv[1]`: invoke `asyncio.run(run_doctor(get_settings()))` and exit with returned code.
- [ ] If `--help` / `-h` is provided: display CLI usage.
- [ ] If `--version` / `-v` is provided: display version number.
- [ ] If no arguments are provided: preserve existing behavior (start HTTP or stdio FastMCP server).

### Phase 3: Unit Tests (`src/opsinsight/doctor_test.py`)
- [ ] Test `mask_dsn` and secret masking functions.
- [ ] Test `check_postgres` for success, connection error, and unconfigured state.
- [ ] Test `check_cloudwatch` for valid credentials, API error, and skipped state.
- [ ] Test `check_newrelic` for 200 OK, GraphQL error, and skipped state.
- [ ] Test `check_server_security` for secure loopback, unauthenticated external warning, and PII status.
- [ ] Test `run_doctor` returns `0` when all configured pass, and `1` when any fail.

### Phase 4: Verification & Integration Gate
- [ ] Run `ruff check .` to ensure 0 lint errors.
- [ ] Run `pytest -m "not integration"` to ensure all unit tests pass and code coverage stays $\ge 85\%$.
- [ ] Test `opsinsight-mcp doctor` manually in terminal to verify real-world formatting.
- [ ] Update `CHANGELOG.md` and `README.md` to document the new `doctor` command.

---

## 7. Acceptance Criteria

1. **Safety:** No secrets appear in output or logs under any circumstance.
2. **Reliability:** Timeout guarantees prevent the command from hanging on unreachable network endpoints.
3. **CI Compatibility:** Exits with `0` on healthy setups and `1` on broken setups.
4. **Code Quality:** Fully passes `ruff check .` and maintains `fail_under = 85` pytest coverage.

