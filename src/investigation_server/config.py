from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _normalize_allowlist(raw: str) -> frozenset[str]:
    return frozenset(item.strip().lower() for item in raw.split(",") if item.strip())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False)

    # Database
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "appdb"
    db_user: str = "mcp_ro"
    db_password: SecretStr = SecretStr("")
    db_table_allowlist: str = "public.orders,public.users,public.payments,public.audit_events"
    db_max_rows: int = 500
    db_statement_timeout_ms: int = 15000
    db_idle_txn_timeout_ms: int = 30000
    db_max_cell_bytes: int = 4096
    db_schema_cache_ttl_s: int = 600

    # AWS / CloudWatch
    aws_region: str = "ap-south-1"
    aws_profile: str | None = None
    cw_log_group_allowlist: str = ""
    cw_max_window_hours: int = 168
    cw_default_window_hours: int = 24
    cw_max_bytes_scanned: int = 5_000_000_000
    cw_poll_max_wait_s: int = 60

    # Server
    audit_log_path: Path = Path("./audit.jsonl")
    pii_redaction: bool = True

    @property
    def db_table_allowlist_set(self) -> frozenset[str]:
        return _normalize_allowlist(self.db_table_allowlist)

    @property
    def cw_log_group_allowlist_set(self) -> frozenset[str]:
        return frozenset(
            item.strip() for item in self.cw_log_group_allowlist.split(",") if item.strip()
        )

    @property
    def db_dsn(self) -> str:
        return (
            f"postgresql+psycopg://{self.db_user}:{self.db_password.get_secret_value()}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
