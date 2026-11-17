import json
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from opsinsight.core.config import Settings
from opsinsight.core.container import Container
from opsinsight.integrations.database.models import (
    Column,
    TableDescription,
    TableSummary,
)
from opsinsight.resources import schema
from opsinsight.service.database_service import DatabaseService


def _use_catalog(monkeypatch: pytest.MonkeyPatch, **methods) -> None:
    """Resources go through DatabaseService (query slot, error envelope);
    only its catalog is faked."""
    service = DatabaseService(Settings(), catalog=SimpleNamespace(**methods))
    monkeypatch.setattr(schema, "get_container", lambda: Container(database_service=service))


async def test_schema_all_tables_serializes_models_as_objects(monkeypatch):
    async def list_tables(_schema, settings=None):
        return [TableSummary(table="public.users", row_estimate=2, comment=None)]

    _use_catalog(monkeypatch, list_tables=list_tables)

    payload = json.loads(await schema.schema_all_tables())

    assert payload == {"tables": [{"table": "public.users", "row_estimate": 2, "comment": None}]}


async def test_schema_table_detail_serializes_nested_models_as_objects(monkeypatch):
    async def describe_table(_name, settings=None):
        return TableDescription(
            table="public.users",
            columns=[Column(name="id", type="integer", nullable=False, default=None)],
            primary_key=["id"],
            foreign_keys=[],
            indexes=[],
        )

    _use_catalog(monkeypatch, describe_table=describe_table)

    payload = json.loads(await schema.schema_table_detail("public.users"))

    assert payload["table"] == "public.users"
    assert payload["columns"] == [{"name": "id", "type": "integer", "nullable": False, "default": None}]


async def test_schema_all_tables_returns_structured_error_when_db_fails(monkeypatch):
    async def list_tables(_schema, settings=None):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    _use_catalog(monkeypatch, list_tables=list_tables)

    payload = json.loads(await schema.schema_all_tables())

    assert payload["ok"] is False
    assert payload["error"]["rule"] == "query_execution_error"
    assert "connection refused" in payload["error"]["detail"]
