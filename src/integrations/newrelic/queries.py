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
