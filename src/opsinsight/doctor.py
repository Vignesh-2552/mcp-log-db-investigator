"""opsinsight-mcp doctor — diagnostic pre-flight checks.

Validates environment variables, network connectivity, authentication
credentials, and safety guarantees (read-only enforcement, PII redaction)
across all three observability pillars without booting the full MCP server.
"""

from __future__ import annotations

import asyncio
import enum
import re
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import opsinsight
from opsinsight.core.config import Settings, get_settings

# ---------------------------------------------------------------------------
# Types for dependency injection (doctor never imports real clients directly)
# ---------------------------------------------------------------------------

# check_postgres: async callable that receives (dsn, timeout) → dict with
# keys "server_version", "transaction_read_only"
AsyncPgProbe = Callable[[str, int], Awaitable[dict[str, Any]]]

# check_cloudwatch: sync callable that receives Settings → logs-client
LogsClientFactory = Callable[[Settings], Any]

# check_newrelic: async callable that receives (url, api_key, timeout) → dict
AsyncHttpGet = Callable[[str, str, float], Awaitable[dict[str, Any]]]

_DEFAULT_DB_DSN = "postgresql+asyncpg://localhost:5432/appdb"


# ---------------------------------------------------------------------------
# CheckStatus / CheckResult
# ---------------------------------------------------------------------------

class CheckStatus(enum.Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"


@dataclass
class CheckResult:
    name: str
    status: CheckStatus
    latency_ms: float | None = None
    message: str = ""
    error: str | None = None


# ---------------------------------------------------------------------------
# Secret-masking utilities
# ---------------------------------------------------------------------------

_DSN_PASSWORD_RE = re.compile(r"://([^:]+):([^@]+)@")


def mask_dsn(url: str) -> str:
    """Mask the password in a database DSN.

    ``postgresql+asyncpg://user:secret@host/db``
    → ``postgresql+asyncpg://user:****@host/db``
    """
    return _DSN_PASSWORD_RE.sub(r"://\1:****@", url)


def mask_key(key: str | None, *, prefix_len: int = 4, suffix_len: int = 3) -> str:
    """Mask an API key or access-key id.

    Shows the first *prefix_len* and last *suffix_len* characters:
    ``AKIAIOSFODNN7EXAMPLE`` → ``AKIA...PLE``.

    Returns ``[NOT SET]`` for ``None`` / empty strings.
    """
    if not key:
        return "[NOT SET]"
    if len(key) <= prefix_len + suffix_len:
        return key[:prefix_len] + "..."
    return key[:prefix_len] + "..." + key[-suffix_len:]


# ---------------------------------------------------------------------------
# Individual check implementations
# ---------------------------------------------------------------------------

async def _default_pg_probe(dsn: str, timeout: int) -> dict[str, Any]:
    """Connect to Postgres via asyncpg, run ``SELECT 1``, check read-only,
    and return version + read-only status."""
    import asyncpg  # imported lazily — tests inject a fake instead

    # asyncpg wants a plain ``postgresql://`` scheme, not the SQLAlchemy
    # dialect prefix ``postgresql+asyncpg://``.
    connect_dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    conn: asyncpg.Connection = await asyncio.wait_for(
        asyncpg.connect(connect_dsn, timeout=timeout),
        timeout=timeout,
    )
    try:
        await conn.fetchval("SELECT 1")
        read_only = await conn.fetchval("SHOW transaction_read_only")
        version = await conn.fetchval("SHOW server_version")
        return {"server_version": version, "transaction_read_only": read_only}
    finally:
        await conn.close()


async def check_postgres(
    settings: Settings,
    *,
    pg_probe: AsyncPgProbe | None = None,
    timeout: int = 3,
) -> CheckResult:
    """Validate PostgreSQL connectivity, read-only status, and latency."""
    dsn = settings.db_dsn
    if dsn == _DEFAULT_DB_DSN and "db_url" not in settings.model_fields_set:
        return CheckResult(
            name="PostgreSQL",
            status=CheckStatus.SKIPPED,
            message="Not configured (DB_URL not set)",
        )

    probe = pg_probe or _default_pg_probe
    t0 = time.perf_counter()
    try:
        info = await asyncio.wait_for(probe(dsn, timeout), timeout=timeout + 1)
    except Exception as exc:  # noqa: BLE001 — diagnostic: catch-all is intentional
        return CheckResult(
            name="PostgreSQL",
            status=CheckStatus.FAIL,
            message=f"{type(exc).__name__}: {_sanitize_pg_error(str(exc), dsn)}",
            error=type(exc).__name__,
        )
    latency = (time.perf_counter() - t0) * 1000.0

    version = info.get("server_version", "unknown")
    read_only = info.get("transaction_read_only", "off")
    ro_label = "read-only verified" if read_only == "on" else "read-only NOT enforced"

    status = CheckStatus.PASS if read_only == "on" else CheckStatus.WARN
    return CheckResult(
        name="PostgreSQL",
        status=status,
        latency_ms=round(latency, 1),
        message=f"Connected ({ro_label}, PostgreSQL {version})",
    )


def _sanitize_pg_error(message: str, dsn: str) -> str:
    """Strip credentials from the raw error message."""
    # Replace the full DSN (with password) if it appears in the message
    masked = mask_dsn(dsn)
    message = message.replace(dsn, masked)
    # Also strip asyncpg-style DSN variants
    plain_dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    message = message.replace(plain_dsn, mask_dsn(plain_dsn))
    return message


# ---- AWS CloudWatch -------------------------------------------------------

def _default_logs_client_factory(settings: Settings) -> Any:
    from opsinsight.integrations.cloudwatch.client import get_logs_client

    return get_logs_client(settings)


def _probe_cw_sync(client: Any, allowlist: frozenset[str]) -> str:
    if allowlist:
        paginator = client.get_paginator("describe_log_groups")
        found_groups: list[str] = []
        for group_name in sorted(allowlist):
            for page in paginator.paginate(logGroupNamePrefix=group_name):
                for lg in page.get("logGroups", []):
                    if lg["logGroupName"] in allowlist:
                        found_groups.append(lg["logGroupName"])
        n = len(found_groups)
        groups_label = ", ".join(sorted(found_groups)) if found_groups else "none matched"
        return f"{n} log group{'s' if n != 1 else ''} accessible ({groups_label})"
    return "Credentials valid (no log group allowlist configured)"


async def check_cloudwatch(
    settings: Settings,
    *,
    logs_client_factory: LogsClientFactory | None = None,
    timeout: float = 10.0,
) -> CheckResult:
    """Validate AWS CloudWatch credentials and log-group accessibility."""
    has_key = bool(settings.cloudwatch_access_key_id)
    has_secret = settings.cloudwatch_secret_access_key is not None
    has_region = bool(settings.cloudwatch_region)
    has_profile = bool(settings.aws_profile)

    if not ((has_key and has_secret and has_region) or has_profile):
        missing: list[str] = []
        if not has_key:
            missing.append("CLOUDWATCH_ACCESS_KEY_ID")
        if not has_secret:
            missing.append("CLOUDWATCH_SECRET_ACCESS_KEY")
        if not has_region:
            missing.append("CLOUDWATCH_REGION")
        return CheckResult(
            name="AWS CloudWatch",
            status=CheckStatus.SKIPPED,
            message=f"Not configured ({', '.join(missing)} not set)",
        )

    factory = logs_client_factory or _default_logs_client_factory
    t0 = time.perf_counter()
    try:
        client = factory(settings)
        allowlist = settings.cw_log_group_allowlist_set
        detail = await asyncio.wait_for(
            asyncio.to_thread(_probe_cw_sync, client, allowlist),
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 — diagnostic: catch-all is intentional
        latency = (time.perf_counter() - t0) * 1000.0
        return CheckResult(
            name="AWS CloudWatch",
            status=CheckStatus.FAIL,
            latency_ms=round(latency, 1),
            message=f"{type(exc).__name__}: {exc}",
            error=type(exc).__name__,
        )
    latency = (time.perf_counter() - t0) * 1000.0
    return CheckResult(
        name="AWS CloudWatch",
        status=CheckStatus.PASS,
        latency_ms=round(latency, 1),
        message=detail,
    )


# ---- New Relic NerdGraph ---------------------------------------------------

async def _default_nr_probe(url: str, api_key: str, timeout: float) -> dict[str, Any]:
    import httpx

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            url,
            headers={"Api-Key": api_key, "Content-Type": "application/json"},
            json={"query": "{actor{user{email}}}"},
        )
        resp.raise_for_status()
        return resp.json()


async def check_newrelic(
    settings: Settings,
    *,
    http_probe: AsyncHttpGet | None = None,
    timeout: float = 10.0,
) -> CheckResult:
    """Validate New Relic NerdGraph API connectivity."""
    api_key = settings.new_relic_api_key
    account_id = settings.new_relic_account_id

    if not api_key or not account_id:
        missing: list[str] = []
        if not api_key:
            missing.append("NEW_RELIC_API_KEY")
        if not account_id:
            missing.append("NEW_RELIC_ACCOUNT_ID")
        return CheckResult(
            name="New Relic",
            status=CheckStatus.SKIPPED,
            message=f"Not configured ({', '.join(missing)} not set)",
        )

    probe = http_probe or _default_nr_probe
    t0 = time.perf_counter()
    try:
        payload = await asyncio.wait_for(
            probe(
                settings.new_relic_graphql_url,
                api_key.get_secret_value(),
                timeout,
            ),
            timeout=timeout + 1,
        )
    except Exception as exc:  # noqa: BLE001 — diagnostic: catch-all is intentional
        latency = (time.perf_counter() - t0) * 1000.0
        return CheckResult(
            name="New Relic",
            status=CheckStatus.FAIL,
            latency_ms=round(latency, 1),
            message=f"{type(exc).__name__}: {exc}",
            error=type(exc).__name__,
        )
    latency = (time.perf_counter() - t0) * 1000.0

    errors = payload.get("errors")
    if errors:
        return CheckResult(
            name="New Relic",
            status=CheckStatus.FAIL,
            latency_ms=round(latency, 1),
            message=f"NerdGraph error: {errors[0].get('message', str(errors[0]))}",
            error="NerdGraphError",
        )

    return CheckResult(
        name="New Relic",
        status=CheckStatus.PASS,
        latency_ms=round(latency, 1),
        message=f"NerdGraph API authenticated (Account: {account_id})",
    )


# ---- Server & Security Sanity Check --------------------------------------

def check_server_security(settings: Settings) -> CheckResult:
    """Validate transport security, PII redaction, and rate limiting."""
    warnings: list[str] = []
    details: list[str] = []

    # Transport security
    is_loopback = settings.server_host in ("127.0.0.1", "localhost", "::1")
    if is_loopback:
        details.append("Loopback-only")
    else:
        has_token = settings.mcp_auth_token is not None
        if has_token:
            details.append(f"External ({settings.server_host}), auth token set")
        elif settings.allow_unauthenticated_http:
            warnings.append(
                f"External ({settings.server_host}) with no auth token "
                "(ALLOW_UNAUTHENTICATED_HTTP=true)"
            )
            details.append(f"External ({settings.server_host}), unauthenticated (proxy assumed)")
        else:
            return CheckResult(
                name="Server Security",
                status=CheckStatus.FAIL,
                message=(
                    f"SERVER_HOST={settings.server_host} (non-loopback) but "
                    "MCP_AUTH_TOKEN is not set and ALLOW_UNAUTHENTICATED_HTTP is false"
                ),
                error="InsecureTransport",
            )

    # PII Redaction
    if settings.pii_redaction:
        details.append("PII redaction active")
    else:
        warnings.append("PII_REDACTION is disabled")

    # Rate limiting
    if settings.rate_limit_rps > 0:
        details.append(f"Rate limit: {settings.rate_limit_rps} rps")
    else:
        warnings.append("Rate limiting disabled (RATE_LIMIT_RPS=0)")

    if warnings:
        status = CheckStatus.WARN
        message = "; ".join(details) + " | Warnings: " + "; ".join(warnings)
    else:
        status = CheckStatus.PASS
        message = ", ".join(details)

    return CheckResult(name="Server Security", status=status, message=message)


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

_SEPARATOR = "-" * 80
_HEADER_LINE = "=" * 80


def format_report(results: list[CheckResult], version: str) -> str:
    """Render the diagnostic report as an aligned text table."""
    lines: list[str] = []
    lines.append(f"opsinsight-mcp doctor (v{version})")
    lines.append(_HEADER_LINE)
    lines.append(f"{'Service':<23}{'Status':<10}{'Latency':<11}Details")
    lines.append(_SEPARATOR)

    n_pass = n_warn = n_fail = 0
    for r in results:
        latency_str = f"{r.latency_ms:.0f}ms" if r.latency_ms is not None else "-"
        status_str = r.status.value
        lines.append(f"{r.name:<23}{status_str:<10}{latency_str:<11}{r.message}")

        if r.status in (CheckStatus.PASS, CheckStatus.SKIPPED):
            n_pass += 1
        elif r.status == CheckStatus.WARN:
            n_warn += 1
        else:
            n_fail += 1

    lines.append(_HEADER_LINE)

    parts: list[str] = []
    parts.append(f"{n_pass} passed")
    parts.append(f"{n_warn} warning{'s' if n_warn != 1 else ''}")
    parts.append(f"{n_fail} error{'s' if n_fail != 1 else ''}")
    summary = ", ".join(parts)

    if n_fail > 0:
        summary += ". Please check your configuration."
    elif n_warn > 0:
        summary += ". Review warnings above."
    else:
        summary += ". All configured services healthy!"

    lines.append(f"Result: {summary}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Top-level runner
# ---------------------------------------------------------------------------

async def run_doctor(settings: Settings | None = None) -> int:
    """Run all diagnostic checks and print the report.

    Returns 0 when every configured check passes (or is skipped), 1 when any
    configured check fails.
    """
    settings = settings or get_settings()

    results: list[CheckResult] = []
    results.append(await check_postgres(settings))
    results.append(await check_cloudwatch(settings))
    results.append(await check_newrelic(settings))
    results.append(check_server_security(settings))

    report = format_report(results, opsinsight.__version__)
    print(report, file=sys.stderr)

    has_failure = any(r.status == CheckStatus.FAIL for r in results)
    return 1 if has_failure else 0
