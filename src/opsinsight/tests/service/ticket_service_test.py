import httpx
from pydantic import SecretStr

from opsinsight.core.config import Settings
from opsinsight.core.errors import ToolError
from opsinsight.integrations.tickets.models import (
    CommentPage,
    Ticket,
    TicketComment,
    TicketPerson,
    TicketSearchHit,
)
from opsinsight.service.ticket_service import CONTENT_TRUST, TicketService


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, linear_api_key=SecretStr("lin_api_x"), **overrides)


def _ticket(description: str = "Order 88213 failed for a@b.test") -> Ticket:
    return Ticket(
        provider="linear", id="uuid", key="ENG-1", title="Checkout", description=description,
        status="Todo", url="https://linear.app/x/ENG-1", created_at="c", updated_at="u",
        assignee=TicketPerson("asha", "u1"), reporter=None,
    )


def _comment(i: int, body: str = "ok") -> TicketComment:
    return TicketComment(id=str(i), author=TicketPerson("N", "a"), body=body, created_at="c", updated_at="u")


class FakeProvider:
    name = "linear"

    def __init__(self, ticket=None, comments=None, hits=None, error: Exception | None = None):
        self.ticket = ticket or _ticket()
        self.comments = comments if comments is not None else CommentPage([_comment(1)], None, False)
        self.hits = hits or []
        self.error = error
        self.calls: list[tuple] = []

    async def get_issue(self, ticket_id):
        self.calls.append(("get_issue", ticket_id))
        if self.error:
            raise self.error
        return self.ticket

    async def search_issues(self, text, jql, limit):
        self.calls.append(("search_issues", text, jql, limit))
        if self.error:
            raise self.error
        return self.hits

    async def list_comments(self, ticket_id, limit):
        self.calls.append(("list_comments", ticket_id, limit))
        if self.error:
            raise self.error
        return self.comments


def _service(provider: FakeProvider, **overrides) -> TicketService:
    return TicketService(_settings(**overrides), providers={"linear": lambda s: provider})


async def test_get_ticket_normalizes_id_redacts_and_marks_untrusted():
    provider = FakeProvider()

    result = await _service(provider).get_ticket("eng-1")

    assert result["ok"] is True
    assert provider.calls == [("get_issue", "ENG-1")]
    assert result["data"]["description"] == "Order 88213 failed for [REDACTED_EMAIL]"  # order id survives
    assert result["data"]["assignee"] == {"display_name": "asha", "account_id": "u1"}
    assert result["meta"]["content_trust"] == CONTENT_TRUST
    assert result["meta"]["provider"] == "linear"
    assert result["meta"]["truncated"] is False
    assert "comments" not in result["data"]


async def test_redaction_follows_the_pii_setting():
    result = await _service(FakeProvider(), pii_redaction=False).get_ticket("ENG-1")
    assert "a@b.test" in result["data"]["description"]


async def test_include_comments_adds_them_with_counts():
    page = CommentPage([_comment(1), _comment(2, "call +91 9876543210")], total=None, has_more=True)
    provider = FakeProvider(comments=page)

    result = await _service(provider, ticket_max_results=7).get_ticket("ENG-1", include_comments=True)

    assert provider.calls[1] == ("list_comments", "ENG-1", 7)
    assert [c["id"] for c in result["data"]["comments"]] == ["1", "2"]
    assert "9876543210" not in result["data"]["comments"][1]["body"]
    assert result["meta"]["comments_returned"] == 2
    assert result["meta"]["comments_has_more"] is True


async def test_long_description_is_truncated_after_redaction():
    provider = FakeProvider(ticket=_ticket("x" * 2000 + " a@b.test"))

    result = await _service(provider, ticket_max_description_bytes=300).get_ticket("ENG-1")

    assert result["meta"]["truncated"] is True
    assert "...[truncated" in result["data"]["description"]
    assert "a@b.test" not in result["data"]["description"]


async def test_comments_past_the_response_budget_are_dropped():
    page = CommentPage([_comment(i, "y" * 400) for i in range(20)], total=20, has_more=False)

    result = await _service(FakeProvider(comments=page), ticket_max_response_bytes=2500).get_ticket(
        "ENG-1", include_comments=True
    )

    assert 0 < result["meta"]["comments_returned"] < 20
    assert result["meta"]["truncated"] is True
    assert result["meta"]["comments_has_more"] is True


async def test_guardrail_errors_never_reach_the_provider():
    provider = FakeProvider()
    result = await _service(provider).get_ticket("not an id")
    assert result["error"]["rule"] == "invalid_ticket_id"
    assert provider.calls == []


async def test_missing_credentials_is_structured():
    service = TicketService(Settings(_env_file=None), providers={"linear": lambda s: FakeProvider()})
    result = await service.get_ticket("ENG-1")
    assert result["ok"] is False
    assert result["error"]["rule"] == "missing_credentials"


async def test_provider_tool_errors_pass_through():
    provider = FakeProvider(error=ToolError(rule="ticket_not_found", message="no"))
    result = await _service(provider).get_ticket("ENG-1")
    assert result["error"]["rule"] == "ticket_not_found"


def _status_error(status: int, text: str = "", headers: dict | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.test")
    response = httpx.Response(status, text=text, headers=headers, request=request)
    return httpx.HTTPStatusError("err", request=request, response=response)


async def test_http_errors_map_to_rules():
    cases = {
        401: "auth_failed",
        403: "auth_failed",
        404: "ticket_not_found",
        429: "rate_limited",
        400: "invalid_query",
        502: "ticket_http_error",
    }
    for status, rule in cases.items():
        result = await _service(FakeProvider(error=_status_error(status, headers={"Retry-After": "30"}))).get_ticket(
            "ENG-1"
        )
        assert result["error"]["rule"] == rule, status
    limited = await _service(FakeProvider(error=_status_error(429, headers={"Retry-After": "30"}))).get_ticket("ENG-1")
    assert "30s" in limited["error"]["message"]


async def test_error_detail_is_redacted():
    result = await _service(FakeProvider(error=_status_error(502, text="user a@b.test broke"))).get_ticket("ENG-1")
    assert "a@b.test" not in result["error"]["detail"]


async def test_timeout_and_connection_errors():
    timeout = await _service(FakeProvider(error=httpx.ReadTimeout("slow"))).get_ticket("ENG-1")
    assert timeout["error"]["rule"] == "upstream_timeout"
    down = await _service(FakeProvider(error=httpx.ConnectError("refused"))).get_ticket("ENG-1")
    assert down["error"]["rule"] == "ticket_connection_error"


async def test_search_clamps_and_reports():
    hit = TicketSearchHit("linear", "u", "ENG-1", "Checkout", "Todo", "x", "u", None)
    provider = FakeProvider(hits=[hit])

    result = await _service(provider, ticket_max_results=10).search_tickets("checkout", limit=99)

    assert provider.calls == [("search_issues", "checkout", None, 10)]
    assert result["data"]["tickets"][0]["key"] == "ENG-1"
    assert result["data"]["note"] is None
    assert result["meta"]["limit_clamped"] is True
    assert result["meta"]["row_count"] == 1


async def test_search_with_no_hits_adds_a_note_and_rejects_jql_for_linear():
    service = _service(FakeProvider())
    empty = await service.search_tickets("nothing")
    assert empty["data"]["tickets"] == []
    assert empty["data"]["note"]
    rejected = await service.search_tickets(jql="project = X")
    assert rejected["error"]["rule"] == "invalid_query"


async def test_list_comments_defaults_to_max_results():
    page = CommentPage([_comment(1)], total=3, has_more=True)
    provider = FakeProvider(comments=page)

    result = await _service(provider, ticket_max_results=25).list_comments("ENG-1")

    assert provider.calls == [("list_comments", "ENG-1", 25)]
    assert result["data"]["ticket_id"] == "ENG-1"
    assert result["meta"]["comments_total"] == 3
    assert result["meta"]["limit_clamped"] is False
    assert result["meta"]["content_trust"] == CONTENT_TRUST


async def test_provider_instances_are_reused():
    built: list[int] = []

    def factory(settings):
        built.append(1)
        return FakeProvider()

    service = TicketService(_settings(), providers={"linear": factory})
    await service.get_ticket("ENG-1")
    await service.list_comments("ENG-1")
    assert built == [1]
