from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False)

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
    audit_log_path: Path = Path("./audit.jsonl")
    pii_redaction: bool = True
    log_level: str = "INFO"
    log_format: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    @property
    def cw_log_group_allowlist_set(self) -> frozenset[str]:
        return frozenset(
            item.strip() for item in self.cw_log_group_allowlist.split(",") if item.strip()
        )

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
