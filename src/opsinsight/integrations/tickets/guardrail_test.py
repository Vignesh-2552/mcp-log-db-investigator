import pytest
from pydantic import SecretStr

from opsinsight.core.config import Settings
from opsinsight.core.errors import TicketGuardrailError
from opsinsight.integrations.tickets.guardrail import (
    SearchSpec,
    build_jira_jql,
    clamp_limit,
    select_provider,
    validate_search,
    validate_ticket_id,
)

JIRA = {"jira_base_url": "https://acme.atlassian.net", "jira_email": "bot@acme.test", "jira_api_token": SecretStr("t")}
LINEAR = {"linear_api_key": SecretStr("lin_api_x")}


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def _rule(fn, *args) -> str:
    with pytest.raises(TicketGuardrailError) as exc:
        fn(*args)
    return exc.value.rule


def test_auto_picks_the_only_configured_provider():
    assert select_provider(None, _settings(**LINEAR)) == "linear"
    assert select_provider(None, _settings(**JIRA)) == "jira"


def test_auto_with_neither_or_both_is_an_error():
    assert _rule(select_provider, None, _settings()) == "missing_credentials"
    assert _rule(select_provider, None, _settings(**LINEAR, **JIRA)) == "provider_ambiguous"


def test_explicit_provider_wins_and_must_be_configured():
    both = _settings(**LINEAR, **JIRA)
    assert select_provider("jira", both) == "jira"
    assert select_provider(" Linear ", both) == "linear"
    assert _rule(select_provider, "jira", _settings(**LINEAR)) == "missing_credentials"
    assert _rule(select_provider, "github", both) == "invalid_provider"


def test_ticket_provider_setting_is_the_default():
    assert select_provider(None, _settings(ticket_provider="jira", **LINEAR, **JIRA)) == "jira"


def test_jira_needs_all_three_settings():
    partial = _settings(jira_base_url="https://acme.atlassian.net", jira_email="a@b.test")
    assert _rule(select_provider, "jira", partial) == "missing_credentials"


@pytest.mark.parametrize(
    ("raw", "provider", "expected"),
    [
        ("ENG-123", "linear", "ENG-123"),
        (" eng-7 ", "linear", "ENG-7"),
        ("PROJ_X-456", "jira", "PROJ_X-456"),
        ("10023", "jira", "10023"),
        ("3F2504E0-4F89-11D3-9A0C-0305E82C3301", "linear", "3f2504e0-4f89-11d3-9a0c-0305e82c3301"),
    ],
)
def test_valid_ticket_ids(raw, provider, expected):
    assert validate_ticket_id(raw, provider) == expected


@pytest.mark.parametrize(
    ("raw", "provider"),
    [
        ("", "linear"),
        ("10023", "linear"),  # numeric ids are Jira-only
        ("3f2504e0-4f89-11d3-9a0c-0305e82c3301", "jira"),  # UUIDs are Linear-only
        ("ENG-12; DROP", "jira"),
        ("../ENG-1", "jira"),
        ("ENG 12", "linear"),
    ],
)
def test_invalid_ticket_ids(raw, provider):
    assert _rule(validate_ticket_id, raw, provider) == "invalid_ticket_id"


def test_search_needs_exactly_one_of_query_or_jql():
    assert validate_search("checkout failed", None, "linear") == SearchSpec(text="checkout failed")
    assert validate_search(None, "project = SUP", "jira") == SearchSpec(jql="project = SUP")
    assert _rule(validate_search, "a", "project = SUP", "jira") == "invalid_query"
    assert _rule(validate_search, "  ", None, "jira") == "invalid_query"


def test_jql_is_jira_only_and_length_capped():
    assert _rule(validate_search, None, "project = SUP", "linear") == "invalid_query"
    assert _rule(validate_search, None, "x" * 1001, "jira") == "invalid_query"
    assert _rule(validate_search, "x" * 201, None, "jira") == "invalid_query"
    assert _rule(validate_search, None, "project = SUP\x00", "jira") == "invalid_query"


def test_control_characters_in_text_become_spaces():
    assert validate_search("a\nb", None, "linear").text == "a b"


def test_text_search_is_escaped_into_a_text_clause():
    jql = build_jira_jql(SearchSpec(text='say "hi" \\ bye'))
    assert jql == 'text ~ "say \\"hi\\" \\\\ bye" ORDER BY updated DESC'


def test_jql_keeps_its_own_order():
    assert build_jira_jql(SearchSpec(jql="project = SUP order by created")) == "project = SUP order by created"
    assert build_jira_jql(SearchSpec(jql="project = SUP")) == "project = SUP ORDER BY updated DESC"


def test_clamp_limit_reports_clamping():
    assert clamp_limit(None, 50, 20) == (20, False)
    assert clamp_limit(10, 50, 20) == (10, False)
    assert clamp_limit(500, 50, 20) == (50, True)
    assert clamp_limit(0, 50, 20) == (1, True)
