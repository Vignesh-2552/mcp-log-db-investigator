"""Atlassian Document Format → plain text.

Jira Cloud's REST API v3 returns descriptions and comment bodies as ADF JSON
(a tree of typed nodes), not text. The client model only needs readable text,
so this flattens the tree, keeping paragraph breaks, list markers, code blocks,
mentions and links. Unknown node types fall back to their children's text."""

from typing import Any

_BLOCK_TYPES = {"paragraph", "heading", "blockquote", "codeBlock", "rule", "panel", "mediaSingle", "table"}


def adf_to_text(node: Any) -> str:
    if node is None:
        return ""
    if isinstance(node, str):  # API v2 / already plain text
        return node
    if not isinstance(node, dict):
        return str(node)
    return _collapse(_render(node, list_depth=0))


def _collapse(text: str) -> str:
    lines = [line.rstrip() for line in text.split("\n")]
    out: list[str] = []
    for line in lines:
        if line == "" and (not out or out[-1] == ""):
            continue
        out.append(line)
    return "\n".join(out).strip()


def _children(node: dict, list_depth: int) -> str:
    return "".join(_render(child, list_depth) for child in node.get("content") or [] if isinstance(child, dict))


def _render(node: dict, list_depth: int) -> str:
    kind = node.get("type")
    attrs = node.get("attrs") or {}

    if kind == "text":
        text = node.get("text") or ""
        for mark in node.get("marks") or []:
            if mark.get("type") == "link":
                href = (mark.get("attrs") or {}).get("href")
                if href and href != text:
                    text = f"{text} ({href})"
            elif mark.get("type") == "code":
                text = f"`{text}`"
        return text
    if kind == "hardBreak":
        return "\n"
    if kind == "mention":
        label = attrs.get("text") or attrs.get("id") or "user"
        return label if label.startswith("@") else f"@{label}"
    if kind == "emoji":
        return attrs.get("text") or attrs.get("shortName") or ""
    if kind in ("inlineCard", "blockCard", "embedCard"):
        return attrs.get("url") or ""
    if kind == "date":
        return str(attrs.get("timestamp") or "")
    if kind == "status":
        return f"[{attrs.get('text') or ''}]"
    if kind == "rule":
        return "\n---\n"
    if kind == "codeBlock":
        return f"\n```\n{_children(node, list_depth)}\n```\n"
    if kind == "blockquote":
        inner = _collapse(_children(node, list_depth))
        return "\n" + "\n".join(f"> {line}" for line in inner.split("\n")) + "\n"
    if kind in ("bulletList", "orderedList"):
        start = int(attrs.get("order") or 1)
        items = [child for child in node.get("content") or [] if isinstance(child, dict)]
        indent = "  " * list_depth
        lines = []
        for index, item in enumerate(items):
            marker = f"{start + index}." if kind == "orderedList" else "-"
            body = _collapse(_children(item, list_depth + 1))
            first, *rest = [line for line in body.split("\n") if line.strip()] or [""]
            lines.append(f"{indent}{marker} {first}")
            # Nested lists already carry their own indent.
            lines.extend(line if line.startswith(indent + "  ") else f"{indent}  {line}" for line in rest)
        return "\n" + "\n".join(lines) + "\n"
    if kind == "tableRow":
        cells = [_collapse(_children(cell, list_depth)).replace("\n", " ") for cell in node.get("content") or []]
        return " | ".join(cells) + "\n"
    if kind in ("media", "mediaGroup"):
        return "[attachment]"
    if kind in _BLOCK_TYPES or kind == "doc":
        return f"\n{_children(node, list_depth)}\n"
    return _children(node, list_depth)
