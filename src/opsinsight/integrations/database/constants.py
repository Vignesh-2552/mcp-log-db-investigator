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
    # SELECT ... INTO creates a table; FOR UPDATE/SHARE takes row locks.
    exp.Into,
    exp.Lock,
)

# Reject calls to these functions anywhere in the tree (matched on the bare,
# lower-cased name, so `pg_catalog.pg_sleep(...)` and `"pg_sleep"(...)` are caught).
DANGEROUS_FUNCTIONS = {
    "pg_read_file",
    "pg_read_binary_file",
    "pg_stat_file",
    "pg_terminate_backend",
    "pg_cancel_backend",
    "pg_reload_conf",
    "pg_rotate_logfile",
    "pg_notify",
    "pg_promote",
    "pg_switch_wal",
    # Change session state that outlives a pooled connection's transaction,
    # or write (rejected by the read-only session anyway; blocked here too).
    "set_config",
    "nextval",
    "setval",
    # Execute a SQL string, which would bypass every check in this module.
    "query_to_xml",
    "query_to_xmlschema",
    "query_to_xml_and_xmlschema",
    "cursor_to_xml",
    "cursor_to_xmlschema",
    # Serialize whole schemas/databases: unbounded output.
    "database_to_xml",
    "database_to_xmlschema",
    "database_to_xml_and_xmlschema",
    "schema_to_xml",
    "schema_to_xmlschema",
    "schema_to_xml_and_xmlschema",
}

# Whole function families, by name prefix: dblink* opens a separate
# connection that is NOT read-only (dblink_exec can run DDL/DML); pg_sleep*
# and advisory locks tie up connections (session-level advisory locks survive
# the transaction rollback and stay held on the pooled connection); the rest
# touch the server filesystem, large objects, replication or WAL.
DANGEROUS_FUNCTION_PREFIXES = (
    "dblink",
    "pg_sleep",
    "pg_advisory",
    "pg_try_advisory",
    "pg_ls_",
    "pg_file_",
    "lo_",
    "pg_logical_",
    "pg_replication_",
    "pg_create_",
    "pg_drop_",
    "pg_copy_",
    "pg_wal_",
    "pg_backup_",
    "pg_start_backup",
    "pg_stop_backup",
)

# Longest SQL text accepted. Bounds parser CPU/memory on hostile input.
MAX_SQL_CHARS = 50_000

# Cap on number of tables searched per fan-out in search_by_identifier/resolve_store.
MAX_SEARCH_TARGETS = 25

# Targets probed concurrently per search_by_identifier/resolve_store call, so
# one call can't hold more than this many pooled connections at once.
SEARCH_FANOUT_CONCURRENCY = 3

# Ordered candidate keys checked on matched rows to extract a store id in resolve_store.
STORE_ID_KEYS = ("store_id", "id")

TLD_RE = re.compile(r"\.[a-zA-Z]{2,}$")
