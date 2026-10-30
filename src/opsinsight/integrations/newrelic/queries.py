NRQL_GRAPHQL_QUERY = """
query($accountId: Int!, $nrql: Nrql!) {
  actor {
    account(id: $accountId) {
      nrql(query: $nrql) {
        results
        metadata {
          eventTypes
          facets
        }
      }
    }
  }
}
"""


def build_keyset_query(event_type: str, window_hours: int) -> str:
    return f"SELECT keyset() FROM {event_type} SINCE {window_hours} hour ago"


def build_show_event_types_query(window_hours: int) -> str:
    return f"SHOW EVENT TYPES SINCE {window_hours} hour ago"


def build_trace_query(event_type: str, attribute: str, literal: str, since_ms: int, until_ms: int) -> str:
    """`event_type`/`attribute` must already be validated and `literal`
    escaped by the caller (`validate_attribute_name`/`build_nrql_string_literal`)."""
    return (
        f"SELECT * FROM {event_type} WHERE `{attribute}` = '{literal}' "
        f"SINCE {since_ms} UNTIL {until_ms}"
    )
