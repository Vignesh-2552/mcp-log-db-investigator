"""Unit tests for opsinsight.doctor — 100% offline, no real clients."""

from __future__ import annotations

from typing import Any, Self
from unittest.mock import MagicMock

from pydantic import SecretStr

from opsinsight.core.config import Settings
from opsinsight.doctor import (
    CheckResult,
    CheckStatus,
    _default_logs_client_factory,
    check_cloudwatch,
    check_newrelic,
    check_postgres,
    check_server_security,
    format_report,
    mask_dsn,
    mask_key,
    run_doctor,
    sanitize_error,
)

# ---------------------------------------------------------------------------
# Helpers to build settings with specific fields
# ---------------------------------------------------------------------------


def _settings(**overrides: Any) -> Settings:
    """Build a Settings with safe defaults and the given overrides.

    Uses ``_env_file=None`` so pydantic-settings does not read ``.env``.
    """
    defaults: dict[str, Any] = {"pii_redaction": True, "rate_limit_rps": 5.0}
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


class FakeConn:
    def __init__(self, version: str = "15.3", default_ro: str = "on", txn_ro: str = "on") -> None:
        self.version = version
        self.default_ro = default_ro
        self.txn_ro = txn_ro

    async def execute(self, statement: Any) -> None:
        pass

    async def scalar(self, statement: Any) -> Any:
        text_sql = str(statement).upper()
        if "SERVER_VERSION" in text_sql:
            return self.version
        if "DEFAULT_TRANSACTION_READ_ONLY" in text_sql:
            return self.default_ro
        if "TRANSACTION_READ_ONLY" in text_sql:
            return self.txn_ro
        return None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass


class FakeEngine:
    def __init__(self, conn: FakeConn | None = None) -> None:
        self.conn = conn or FakeConn()

    def connect(self) -> FakeConn:
        return self.conn


# ---------------------------------------------------------------------------
# mask_dsn & sanitize_error
# ---------------------------------------------------------------------------


class TestMaskDsn:
    def test_masks_password(self):
        dsn = "postgresql+asyncpg://user:s3cr3t@host:5432/db"
        assert mask_dsn(dsn) == "postgresql+asyncpg://user:***@host:5432/db"

    def test_no_password(self):
        dsn = "postgresql+asyncpg://host:5432/db"
        assert mask_dsn(dsn) == dsn

    def test_special_characters_in_password(self):
        # Password contains '@' unencoded — must not leave leftover fragments like 'ss:w0rd!'
        dsn = "postgresql+asyncpg://admin:p@ss:w0rd!@myhost:5432/mydb"
        result = mask_dsn(dsn)
        assert "p@ss:w0rd!" not in result
        assert "ss:w0rd!" not in result
        assert "w0rd!" not in result
        assert "****@" in result

    def test_multiple_at_symbols_in_password(self):
        dsn = "postgresql+asyncpg://admin:pass@@word@host:5432/db"
        result = mask_dsn(dsn)
        assert "pass@@word" not in result
        assert "****@" in result

    def test_invalid_dsn_fallback(self):
        assert mask_dsn("not-a-valid-url") == "[INVALID_DSN]"


class TestSanitizeError:
    def test_sanitizes_embedded_dsn_and_secrets(self):
        secret_dsn = "postgresql+asyncpg://admin:p@ss:w0rd!@myhost:5432/mydb"
        settings = _settings(
            db_url=SecretStr(secret_dsn),
            cloudwatch_secret_access_key=SecretStr("super_aws_secret_key"),
            new_relic_api_key=SecretStr("nr-test-api-key"),
            mcp_auth_token=SecretStr("bearer_token_secret"),
        )
        raw_error = (
            f"Failed to connect: {secret_dsn} with key super_aws_secret_key and "
            f"token bearer_token_secret and nr-test-api-key"
        )
        cleaned = sanitize_error(raw_error, settings)

        assert "p@ss:w0rd!" not in cleaned
        assert "ss:w0rd!" not in cleaned
        assert "super_aws_secret_key" not in cleaned
        assert "bearer_token_secret" not in cleaned
        assert "nr-test-api-key" not in cleaned
        assert "****@" in cleaned


# ---------------------------------------------------------------------------
# mask_key
# ---------------------------------------------------------------------------


class TestMaskKey:
    def test_normal_key(self):
        assert mask_key("AKIAIOSFODNN7EXAMPLE") == "AKIA...PLE"

    def test_short_key(self):
        assert mask_key("NRAK") == "NRAK..."

    def test_none_returns_not_set(self):
        assert mask_key(None) == "[NOT SET]"

    def test_empty_returns_not_set(self):
        assert mask_key("") == "[NOT SET]"

    def test_custom_lengths(self):
        assert mask_key("ABCDEFGHIJ", prefix_len=2, suffix_len=2) == "AB...IJ"


# ---------------------------------------------------------------------------
# check_postgres
# ---------------------------------------------------------------------------


class TestCheckPostgres:
    async def test_pass_with_read_only(self):
        engine = FakeEngine(FakeConn(version="15.3", default_ro="on", txn_ro="on"))
        settings = _settings(db_url=SecretStr("postgresql+asyncpg://u:p@host/db"))
        result = await check_postgres(settings, engine_factory=lambda s: engine)

        assert result.status == CheckStatus.PASS
        assert result.latency_ms is not None
        assert "read-only verified" in result.message
        assert "15.3" in result.message

    async def test_warn_when_not_read_only(self):
        # When default_transaction_read_only is 'off', session is not hardened
        engine = FakeEngine(FakeConn(version="14.0", default_ro="off", txn_ro="off"))
        settings = _settings(db_url=SecretStr("postgresql+asyncpg://u:p@host/db"))
        result = await check_postgres(settings, engine_factory=lambda s: engine)

        assert result.status == CheckStatus.WARN
        assert "read-only NOT enforced" in result.message

    async def test_fail_on_connection_error(self):
        class BrokenEngine:
            def connect(self):
                raise ConnectionRefusedError("Host unreachable")

        settings = _settings(db_url=SecretStr("postgresql+asyncpg://u:p@host/db"))
        result = await check_postgres(settings, engine_factory=lambda s: BrokenEngine())

        assert result.status == CheckStatus.FAIL
        assert "ConnectionRefusedError" in result.message
        assert result.error == "ConnectionRefusedError"

    async def test_skip_when_unconfigured(self):
        settings = _settings()
        result = await check_postgres(settings)

        assert result.status == CheckStatus.SKIPPED
        assert "Not configured" in result.message

    async def test_credentials_sanitized_in_error(self):
        secret_dsn = "postgresql+asyncpg://admin:p@ss:w0rd!@dbhost:5432/mydb"

        class FailingEngine:
            def connect(self):
                raise RuntimeError(f"could not connect to {secret_dsn}")

        settings = _settings(db_url=SecretStr(secret_dsn))
        result = await check_postgres(settings, engine_factory=lambda s: FailingEngine())

        assert result.status == CheckStatus.FAIL
        assert "p@ss:w0rd!" not in result.message
        assert "ss:w0rd!" not in result.message
        assert "****@" in result.message


# ---------------------------------------------------------------------------
# check_cloudwatch
# ---------------------------------------------------------------------------


class TestCheckCloudWatch:
    async def test_pass_with_log_groups(self):
        mock_client = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [
            {"logGroups": [{"logGroupName": "corksy"}]}
        ]
        mock_client.get_paginator.return_value = mock_paginator

        settings = _settings(
            cloudwatch_access_key_id="AKIAEXAMPLE",
            cloudwatch_secret_access_key=SecretStr("secret"),
            cloudwatch_region="us-east-1",
            cloudwatch_allowed_log_group="corksy",
        )
        result = await check_cloudwatch(
            settings, logs_client_factory=lambda s: mock_client
        )

        assert result.status == CheckStatus.PASS
        assert "1 log group accessible" in result.message
        assert "corksy" in result.message

    async def test_pass_without_allowlist_makes_api_call(self):
        # Empty allowlist must call describe_log_groups(limit=1) to verify credentials
        mock_client = MagicMock()
        mock_client.describe_log_groups.return_value = {"logGroups": []}
        settings = _settings(
            cloudwatch_access_key_id="AKIAEXAMPLE",
            cloudwatch_secret_access_key=SecretStr("secret"),
            cloudwatch_region="us-east-1",
        )
        result = await check_cloudwatch(
            settings, logs_client_factory=lambda s: mock_client
        )

        assert result.status == CheckStatus.PASS
        assert "no log group allowlist" in result.message
        mock_client.describe_log_groups.assert_called_once_with(limit=1)

    async def test_fail_without_allowlist_when_credentials_invalid(self):
        # Empty allowlist fails if the credentials call raises an API error
        mock_client = MagicMock()
        mock_client.describe_log_groups.side_effect = RuntimeError("InvalidClientTokenId")

        settings = _settings(
            cloudwatch_access_key_id="AKIAEXAMPLE",
            cloudwatch_secret_access_key=SecretStr("secret"),
            cloudwatch_region="us-east-1",
        )
        result = await check_cloudwatch(
            settings, logs_client_factory=lambda s: mock_client
        )

        assert result.status == CheckStatus.FAIL
        assert "InvalidClientTokenId" in result.message

    async def test_default_factory_configures_bounded_timeouts(self):
        settings = _settings(
            cloudwatch_access_key_id="AKIAEXAMPLE",
            cloudwatch_secret_access_key=SecretStr("secret"),
            cloudwatch_region="us-east-1",
        )
        client = _default_logs_client_factory(settings)
        # Check that botocore client was built with bounded timeout configuration
        config = client.meta.config
        assert config.connect_timeout == 3
        assert config.read_timeout == 5

    async def test_fail_on_api_error_sanitizes_secret(self):
        secret_val = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"

        def factory(s: Settings) -> None:
            raise RuntimeError(f"AWS request failed with secret {secret_val}")

        settings = _settings(
            cloudwatch_access_key_id="AKIAEXAMPLE",
            cloudwatch_secret_access_key=SecretStr(secret_val),
            cloudwatch_region="us-west-1",
        )
        result = await check_cloudwatch(settings, logs_client_factory=factory)

        assert result.status == CheckStatus.FAIL
        assert secret_val not in result.message

    async def test_skip_when_unconfigured(self):
        settings = _settings()
        result = await check_cloudwatch(settings)

        assert result.status == CheckStatus.SKIPPED
        assert "CLOUDWATCH_ACCESS_KEY_ID" in result.message

    async def test_skip_partial_config(self):
        settings = _settings(cloudwatch_access_key_id="AKIAEXAMPLE")
        result = await check_cloudwatch(settings)

        assert result.status == CheckStatus.SKIPPED
        assert "CLOUDWATCH_SECRET_ACCESS_KEY" in result.message
        assert "CLOUDWATCH_REGION" in result.message


# ---------------------------------------------------------------------------
# check_newrelic
# ---------------------------------------------------------------------------


class TestCheckNewRelic:
    async def test_pass_on_200(self):
        async def probe(url: str, api_key: str, timeout: float) -> dict:
            return {"data": {"actor": {"user": {"email": "test@example.com"}}}}

        settings = _settings(
            new_relic_api_key=SecretStr("NRAK-XXXXXXXXXXXXXXXXXXXX"),
            new_relic_account_id="7726086",
        )
        result = await check_newrelic(settings, http_probe=probe)

        assert result.status == CheckStatus.PASS
        assert "7726086" in result.message
        assert result.latency_ms is not None

    async def test_fail_on_graphql_error(self):
        async def probe(url: str, api_key: str, timeout: float) -> dict:
            return {"errors": [{"message": "Invalid API key"}]}

        settings = _settings(
            new_relic_api_key=SecretStr("NRAK-bad"),
            new_relic_account_id="12345",
        )
        result = await check_newrelic(settings, http_probe=probe)

        assert result.status == CheckStatus.FAIL
        assert "Invalid API key" in result.message
        assert result.error == "NerdGraphError"

    async def test_fail_on_network_error_sanitizes_secret(self):
        api_key_val = "NRAK-SECRETKEY123456789012345"

        async def probe(url: str, api_key: str, timeout: float) -> dict:
            raise ConnectionError(f"DNS resolution failed using key {api_key_val}")

        settings = _settings(
            new_relic_api_key=SecretStr(api_key_val),
            new_relic_account_id="12345",
        )
        result = await check_newrelic(settings, http_probe=probe)

        assert result.status == CheckStatus.FAIL
        assert api_key_val not in result.message

    async def test_skip_when_unconfigured(self):
        settings = _settings()
        result = await check_newrelic(settings)

        assert result.status == CheckStatus.SKIPPED
        assert "NEW_RELIC_API_KEY" in result.message

    async def test_skip_partial_config(self):
        settings = _settings(new_relic_api_key=SecretStr("NRAK-XXXX"))
        result = await check_newrelic(settings)

        assert result.status == CheckStatus.SKIPPED
        assert "NEW_RELIC_ACCOUNT_ID" in result.message


# ---------------------------------------------------------------------------
# check_server_security
# ---------------------------------------------------------------------------


class TestCheckServerSecurity:
    def test_pass_loopback(self):
        settings = _settings(server_host="127.0.0.1")
        result = check_server_security(settings)

        assert result.status == CheckStatus.PASS
        assert "Loopback-only" in result.message
        assert "PII redaction active" in result.message
        assert "Rate limit" in result.message

    def test_pass_external_with_token(self):
        settings = _settings(
            server_host="0.0.0.0",
            mcp_auth_token=SecretStr("secret-token"),
        )
        result = check_server_security(settings)

        assert result.status == CheckStatus.PASS
        assert "auth token set" in result.message

    def test_warn_external_unauthenticated(self):
        settings = _settings(
            server_host="0.0.0.0",
            allow_unauthenticated_http=True,
        )
        result = check_server_security(settings)

        assert result.status == CheckStatus.WARN
        assert "unauthenticated" in result.message.lower()

    def test_fail_external_no_auth(self):
        settings = _settings(
            server_host="0.0.0.0",
            allow_unauthenticated_http=False,
        )
        result = check_server_security(settings)

        assert result.status == CheckStatus.FAIL
        assert "MCP_AUTH_TOKEN is not set" in result.message

    def test_warn_pii_disabled(self):
        settings = _settings(
            server_host="127.0.0.1",
            pii_redaction=False,
        )
        result = check_server_security(settings)

        assert result.status == CheckStatus.WARN
        assert "PII_REDACTION is disabled" in result.message

    def test_warn_rate_limit_disabled(self):
        settings = _settings(
            server_host="127.0.0.1",
            rate_limit_rps=0,
        )
        result = check_server_security(settings)

        assert result.status == CheckStatus.WARN
        assert "Rate limiting disabled" in result.message

    def test_loopback_localhost(self):
        settings = _settings(server_host="localhost")
        result = check_server_security(settings)
        assert result.status == CheckStatus.PASS

    def test_loopback_ipv6(self):
        settings = _settings(server_host="::1")
        result = check_server_security(settings)
        assert result.status == CheckStatus.PASS


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------


class TestFormatReport:
    def test_all_pass(self):
        results = [
            CheckResult("PostgreSQL", CheckStatus.PASS, 24.0, "Connected (read-only verified, PostgreSQL 15.3)"),
            CheckResult("AWS CloudWatch", CheckStatus.PASS, 112.0, "1 log group accessible (corksy)"),
            CheckResult("New Relic", CheckStatus.PASS, 185.0, "NerdGraph API authenticated (Account: 7726086)"),
            CheckResult("Server Security", CheckStatus.PASS, None, "Loopback-only, PII redaction active, Rate limit: 5.0 rps"),
        ]
        report = format_report(results, "0.2.0")

        assert "opsinsight-mcp doctor (v0.2.0)" in report
        assert "PASS" in report
        assert "4 passed" in report
        assert "0 errors" in report
        assert "All configured services healthy!" in report

    def test_with_failure(self):
        results = [
            CheckResult("PostgreSQL", CheckStatus.FAIL, None, "ConnectionRefusedError: Host unreachable"),
            CheckResult("AWS CloudWatch", CheckStatus.SKIPPED, None, "Not configured"),
            CheckResult("New Relic", CheckStatus.SKIPPED, None, "Not configured"),
            CheckResult("Server Security", CheckStatus.PASS, None, "Loopback-only"),
        ]
        report = format_report(results, "0.2.0")

        assert "FAIL" in report
        assert "1 error" in report
        assert "Please check your configuration" in report

    def test_with_warning(self):
        results = [
            CheckResult("Server Security", CheckStatus.WARN, None, "PII disabled"),
        ]
        report = format_report(results, "0.2.0")

        assert "WARN" in report
        assert "1 warning" in report
        assert "Review warnings above" in report

    def test_skipped_counts_as_pass(self):
        results = [
            CheckResult("PostgreSQL", CheckStatus.SKIPPED, None, "Not configured"),
        ]
        report = format_report(results, "0.2.0")

        assert "1 passed" in report
        assert "All configured services healthy!" in report

    def test_latency_formatting(self):
        results = [
            CheckResult("PostgreSQL", CheckStatus.PASS, 24.3, "OK"),
        ]
        report = format_report(results, "0.2.0")
        assert "24ms" in report

    def test_no_latency_shows_dash(self):
        results = [
            CheckResult("Server Security", CheckStatus.PASS, None, "OK"),
        ]
        report = format_report(results, "0.2.0")
        lines = report.split("\n")
        security_line = next(line for line in lines if "Server Security" in line)
        assert "-" in security_line


# ---------------------------------------------------------------------------
# run_doctor (integration of all checks)
# ---------------------------------------------------------------------------


class TestRunDoctor:
    async def test_returns_0_when_all_pass(self):
        engine = FakeEngine(FakeConn(version="15.3", default_ro="on", txn_ro="on"))

        async def nr_probe(url: str, key: str, timeout: float) -> dict:
            return {"data": {"actor": {"user": {"email": "a@b.com"}}}}

        mock_cw = MagicMock()
        mock_cw.describe_log_groups.return_value = {"logGroups": []}

        settings = _settings(
            db_url=SecretStr("postgresql+asyncpg://u:p@host/db"),
            cloudwatch_access_key_id="AKIA",
            cloudwatch_secret_access_key=SecretStr("s"),
            cloudwatch_region="us-east-1",
            new_relic_api_key=SecretStr("NRAK-X"),
            new_relic_account_id="12345",
            server_host="127.0.0.1",
        )

        import opsinsight.doctor as doc

        orig_engine = doc._default_engine_factory
        orig_nr = doc._default_nr_probe
        orig_cw = doc._default_logs_client_factory
        doc._default_engine_factory = lambda s: engine
        doc._default_nr_probe = nr_probe
        doc._default_logs_client_factory = lambda s: mock_cw
        try:
            code = await run_doctor(settings)
        finally:
            doc._default_engine_factory = orig_engine
            doc._default_nr_probe = orig_nr
            doc._default_logs_client_factory = orig_cw

        assert code == 0

    async def test_returns_1_when_any_fail(self):
        class BrokenEngine:
            def connect(self):
                raise ConnectionRefusedError("boom")

        settings = _settings(
            db_url=SecretStr("postgresql+asyncpg://u:p@host/db"),
        )

        import opsinsight.doctor as doc

        orig_engine = doc._default_engine_factory
        doc._default_engine_factory = lambda s: BrokenEngine()
        try:
            code = await run_doctor(settings)
        finally:
            doc._default_engine_factory = orig_engine

        assert code == 1

    async def test_returns_0_when_all_skipped(self):
        """All unconfigured → all SKIPPED → exit 0."""
        settings = _settings()
        code = await run_doctor(settings)
        assert code == 0
