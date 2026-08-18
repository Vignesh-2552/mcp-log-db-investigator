import re

from sqlglot import exp

DIALECT = "postgres"

# Reject any of these node types anywhere in the tree.
BLOCKED_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Grant,
    exp.TruncateTable,
    exp.Copy,
    exp.Command,
)

# Reject calls to these functions anywhere in the tree.
DANGEROUS_FUNCTIONS = {
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_sleep",
    "dblink",
    "dblink_connect",
    "lo_import",
    "lo_export",
    "pg_terminate_backend",
    "pg_cancel_backend",
}

# Cap on number of tables searched per fan-out in search_by_identifier/resolve_store.
MAX_SEARCH_TARGETS = 25

# Ordered candidate keys checked on matched rows to extract a store id in resolve_store.
STORE_ID_KEYS = ("store_id", "id")

TLD_RE = re.compile(r"\.[a-zA-Z]{2,}$")
