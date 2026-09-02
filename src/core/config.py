import logging
import os
from functools import lru_cache

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LEGACY_AWS_ENV_VARS = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    db_url: str = "postgresql+asyncpg://localhost:5432/appdb"
    db_max_rows: int = 500
    db_statement_timeout_ms: int = 15000
    db_idle_txn_timeout_ms: int = 30000
    db_max_cell_bytes: int = 4096
    db_schema_cache_ttl_s: int = 600
    db_store_identifier_columns: str = "domain,domain_name,hostname,host,store_name,slug,subdomain"
    db_historical_schema_prefixes: str = "migration"

    # AWS / CloudWatch
    aws_profile: str | None = None
    cloudwatch_access_key_id: str | None = None
    cloudwatch_secret_access_key: SecretStr | None = None
    cloudwatch_region: str | None = None
    cloudwatch_allowed_log_group: str | None = None
    cw_max_window_hours: int = 168
    cw_default_window_hours: int = 24
    cw_max_bytes_scanned: int = 5_000_000_000
    cw_poll_max_wait_s: int = 60
    cw_describe_fields_sample_size: int = 50
    cw_describe_fields_error_boost_size: int = 20

    # New Relic (NerdGraph / NRQL)
    new_relic_api_key: SecretStr | None = None
    new_relic_account_id: str | None = None
    new_relic_region: str = "us"
    nr_max_window_hours: int = 168
    nr_default_window_hours: int = 24
    nr_max_rows: int = 500

    # Server
    # Keep the unauthenticated HTTP transport local by default. Operators who
    # deliberately expose it through an authenticated proxy can override this.
    server_host: str = "127.0.0.1"
    server_port: int = 8000
    server_path: str = "/mcp"
    pii_redaction: bool = True
    log_level: str = "INFO"
    log_format: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    # Shared-secret bearer token required on every MCP request once set (see
    # core/auth.py). Required for any non-loopback SERVER_HOST — the
    # streamable-HTTP transport has no other authentication.
    mcp_auth_token: SecretStr | None = None

    @model_validator(mode="after")
    def _fall_back_server_port_to_platform_port(self) -> "Settings":
        # Render/Railway/Heroku-style platforms assign a listen port via $PORT
        # and expect the app to bind it; only apply this when the operator
        # hasn't explicitly set SERVER_PORT themselves. This class's env
        # lookup is case-insensitive (case_sensitive=False above), so match
        # that here with a case-insensitive scan rather than a literal
        # "SERVER_PORT" in os.environ check — otherwise an operator-set
        # `server_port`/`Server_Port` env var would go undetected and be
        # silently overridden by $PORT.
        platform_port = os.environ.get("PORT")
        server_port_explicitly_set = any(k.upper() == "SERVER_PORT" for k in os.environ)
        if platform_port and not server_port_explicitly_set:
            try:
                self.server_port = int(platform_port)
            except ValueError:
                logging.getLogger("investigation_server.core.config").warning(
                    "$PORT=%r is not a valid integer — ignoring, using SERVER_PORT=%s",
                    platform_port,
                    self.server_port,
                )
        return self

    @model_validator(mode="after")
    def _warn_on_unread_legacy_aws_env_vars(self) -> "Settings":
        # CLOUDWATCH_ACCESS_KEY_ID/CLOUDWATCH_SECRET_ACCESS_KEY/CLOUDWATCH_REGION are the
        # only names this app reads (see integrations/cloudwatch/client.py) — deliberately,
        # with no AWS_* fallback. An operator upgrading from before that rename can still
        # have the old AWS_* names set in their environment/.env and get no signal that
        # they're now silently ignored (extra="ignore"), so warn instead of staying quiet.
        legacy_set = [v for v in _LEGACY_AWS_ENV_VARS if os.environ.get(v)]
        new_set = any([self.cloudwatch_access_key_id, self.cloudwatch_secret_access_key, self.cloudwatch_region])
        if legacy_set and not new_set:
            logging.getLogger("investigation_server.core.config").warning(
                "%s set but not read by this app — CloudWatch tools use "
                "CLOUDWATCH_ACCESS_KEY_ID/CLOUDWATCH_SECRET_ACCESS_KEY/CLOUDWATCH_REGION instead.",
                ", ".join(legacy_set),
            )
        return self

    @property
    def cloudwatch_effective_region(self) -> str | None:
        return self.cloudwatch_region

    @property
    def cloudwatch_effective_access_key_id(self) -> str | None:
        return self.cloudwatch_access_key_id

    @property
    def cloudwatch_effective_secret_access_key(self) -> SecretStr | None:
        return self.cloudwatch_secret_access_key

    @property
    def cw_log_group_allowlist_set(self) -> frozenset[str]:
        if not self.cloudwatch_allowed_log_group:
            return frozenset()
        return frozenset(
            item.strip() for item in self.cloudwatch_allowed_log_group.split(",") if item.strip()
        )

    @property
    def db_dsn(self) -> str:
        return self.db_url

    @property
    def db_store_identifier_columns_set(self) -> frozenset[str]:
        return frozenset(
            item.strip() for item in self.db_store_identifier_columns.split(",") if item.strip()
        )

    @property
    def db_historical_schema_prefixes_set(self) -> frozenset[str]:
        return frozenset(
            item.strip() for item in self.db_historical_schema_prefixes.split(",") if item.strip()
        )

    @property
    def new_relic_graphql_url(self) -> str:
        if self.new_relic_region.lower() == "eu":
            return "https://api.eu.newrelic.com/graphql"
        return "https://api.newrelic.com/graphql"


@lru_cache
def get_settings() -> Settings:
    return Settings()
