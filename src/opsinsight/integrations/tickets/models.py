"""Provider-agnostic ticket shapes. Both the Linear and the Jira client return
these, so `TicketService` never branches on the provider.

People are reduced to a display name and an opaque account id: emails are
never copied out of a provider response."""

from dataclasses import dataclass, field

from opsinsight.core.models import DictableMixin


@dataclass
class TicketPerson(DictableMixin):
    display_name: str | None
    account_id: str | None


@dataclass
class TicketComment(DictableMixin):
    id: str
    author: TicketPerson | None
    body: str
    created_at: str | None
    updated_at: str | None


@dataclass
class Ticket(DictableMixin):
    provider: str
    id: str
    key: str
    title: str
    description: str
    status: str | None
    url: str | None
    created_at: str | None
    updated_at: str | None
    assignee: TicketPerson | None
    reporter: TicketPerson | None
    priority: str | None = None
    labels: list[str] = field(default_factory=list)
    project: str | None = None
    issue_type: str | None = None


@dataclass
class TicketSearchHit(DictableMixin):
    provider: str
    id: str
    key: str
    title: str
    status: str | None
    url: str | None
    updated_at: str | None
    assignee: TicketPerson | None


@dataclass
class CommentPage(DictableMixin):
    comments: list[TicketComment]
    # Total the provider reports, when it reports one (Jira does, Linear doesn't).
    total: int | None
    has_more: bool
