from opsinsight.core.config import Settings
from opsinsight.integrations.database import introspect
from opsinsight.integrations.database.introspect import (
    _domain_search_variants,
    _entity_name_candidates,
    _is_historical_schema,
    _source_type,
)

_HISTORICAL_PREFIXES = frozenset({"migration"})


def test_domain_search_variants_adds_bare_hostname():
    assert _domain_search_variants("olallawines.com") == ["olallawines.com", "olallawines"]


def test_domain_search_variants_no_tld_returns_single_variant():
    assert _domain_search_variants("olallawines") == ["olallawines"]


def test_is_historical_schema_matches_prefix_case_insensitively():
    assert _is_historical_schema("migration", _HISTORICAL_PREFIXES) is True
    assert _is_historical_schema("Migration_2023", _HISTORICAL_PREFIXES) is True
    assert _is_historical_schema("public", _HISTORICAL_PREFIXES) is False


def test_source_type_reflects_historical_prefix():
    assert _source_type("migration", _HISTORICAL_PREFIXES) == "historical"
    assert _source_type("public", _HISTORICAL_PREFIXES) == "live"


def test_entity_name_candidates_unaffected_by_new_changes():
    assert _entity_name_candidates("order_id") == ["order", "orders"]
    assert _entity_name_candidates("category") == ["categories", "category", "categorys"]


async def test_search_targets_prefer_entity_named_tables_under_the_cap(monkeypatch):
    """With more candidate tables than MAX_SEARCH_TARGETS, `order_*` tables
    must survive the cap ahead of unrelated tables that sort earlier."""
    unrelated = [("private", f"a_table_{i:02d}") for i in range(30)]
    monkeypatch.setattr(introspect, "get_engine", lambda settings: None)

    async def columns(engine, column_name):
        return unrelated + [("private", "order_payment")]

    async def entities(engine, candidates):
        return [("private", "orders", "id")]

    async def time_columns(engine, tables):
        return {}

    searched: list[str] = []

    async def gather(coros, limit):
        for c in coros:
            c.close()
        return []

    monkeypatch.setattr(introspect, "_find_tables_with_column", columns)
    monkeypatch.setattr(introspect, "_find_entity_tables", entities)
    monkeypatch.setattr(introspect, "_find_time_columns", time_columns)
    monkeypatch.setattr(introspect, "_gather_bounded", gather)

    result = await introspect.search_by_identifier("42", "order_id", Settings())
    searched = [t.table for t in result.searched_tables]

    assert searched[:2] == ["private.orders", "private.order_payment"]
    assert result.truncated is True
