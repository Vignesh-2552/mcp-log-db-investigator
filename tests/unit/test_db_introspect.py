from integrations.database.introspect import (
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
