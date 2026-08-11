from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # AWS / CloudWatch
    aws_region: str = "ap-south-1"
    aws_profile: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: SecretStr | None = None
    # CLOUDWATCH_* takes priority over the AWS_* fields above when set —
    # lets CloudWatch use a dedicated key/region separate from other AWS
    # usage (e.g. S3) without them stepping on each other in .env.
    cloudwatch_access_key_id: str | None = None
    cloudwatch_secret_access_key: SecretStr | None = None
    cloudwatch_region: str | None = None
    cloudwatch_allowed_log_group: str | None = None
    cw_log_group_allowlist: str = ""
    cw_max_window_hours: int = 168
    cw_default_window_hours: int = 24
    cw_max_bytes_scanned: int = 5_000_000_000
    cw_poll_max_wait_s: int = 60

    # New Relic (NerdGraph / NRQL)
    new_relic_api_key: SecretStr | None = None
    new_relic_account_id: str | None = None
    new_relic_region: str = "us"
    nr_max_window_hours: int = 168
    nr_default_window_hours: int = 24
    nr_max_rows: int = 500

    # Server
    server_host: str = "0.0.0.0"
    server_port: int = 8000
    server_path: str = "/mcp"
    pii_redaction: bool = True
    log_level: str = "INFO"
    log_format: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    @property
    def cloudwatch_effective_region(self) -> str:
        return self.cloudwatch_region or self.aws_region

    @property
    def cloudwatch_effective_access_key_id(self) -> str | None:
        return self.cloudwatch_access_key_id or self.aws_access_key_id

    @property
    def cloudwatch_effective_secret_access_key(self) -> SecretStr | None:
        return self.cloudwatch_secret_access_key or self.aws_secret_access_key

    @property
    def cw_log_group_allowlist_set(self) -> frozenset[str]:
        items = {item.strip() for item in self.cw_log_group_allowlist.split(",") if item.strip()}
        if self.cloudwatch_allowed_log_group:
            items.add(self.cloudwatch_allowed_log_group.strip())
        return frozenset(items)

    @property
    def db_dsn(self) -> str:
        return self.db_url

    @property
    def new_relic_graphql_url(self) -> str:
        if self.new_relic_region.lower() == "eu":
            return "https://api.eu.newrelic.com/graphql"
        return "https://api.newrelic.com/graphql"


@lru_cache
def get_settings() -> Settings:
    return Settings()
