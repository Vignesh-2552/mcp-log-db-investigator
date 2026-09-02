import json

from integrations.database.models import Column, TableDescription, TableSummary
from resources import schema


async def test_schema_all_tables_serializes_models_as_objects(monkeypatch):
    async def list_tables(_schema):
        return [TableSummary(table="public.users", row_estimate=2, comment=None)]

    monkeypatch.setattr(schema.introspect, "list_tables", list_tables)

    payload = json.loads(await schema.schema_all_tables())

    assert payload == {
        "tables": [{"table": "public.users", "row_estimate": 2, "comment": None}]
    }


async def test_schema_table_detail_serializes_nested_models_as_objects(monkeypatch):
    async def describe_table(_name):
        return TableDescription(
            table="public.users",
            columns=[Column(name="id", type="integer", nullable=False, default=None)],
            primary_key=["id"],
            foreign_keys=[],
            indexes=[],
        )

    monkeypatch.setattr(schema.introspect, "describe_table", describe_table)

    payload = json.loads(await schema.schema_table_detail("public.users"))

    assert payload["table"] == "public.users"
    assert payload["columns"] == [
        {"name": "id", "type": "integer", "nullable": False, "default": None}
    ]
