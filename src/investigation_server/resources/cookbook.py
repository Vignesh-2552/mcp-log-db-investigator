from pathlib import Path

import yaml

from investigation_server.core.app import mcp

_COOKBOOK_PATH = Path(__file__).resolve().parent.parent / "data" / "query_cookbook.yaml"


@mcp.resource("docs://query-cookbook")
def query_cookbook() -> str:
    """Curated example queries (SQL + Logs Insights) for common investigations (doc §4.4)."""
    entries = yaml.safe_load(_COOKBOOK_PATH.read_text(encoding="utf-8")) or []
    lines = ["# Query Cookbook", ""]
    for entry in entries:
        lines.append(f"## {entry['title']}")
        lines.append(f"*Kind:* `{entry['kind']}`")
        lines.append("")
        lang = "sql" if entry["kind"] == "sql" else ""
        lines.append(f"```{lang}")
        lines.append(entry["query"].strip())
        lines.append("```")
        if entry.get("notes"):
            lines.append("")
            lines.append(entry["notes"])
        lines.append("")
    return "\n".join(lines)
