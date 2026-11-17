from opsinsight.integrations.tickets.adf import adf_to_text


def _doc(*content):
    return {"type": "doc", "version": 1, "content": list(content)}


def _p(*content):
    return {"type": "paragraph", "content": list(content)}


def _t(text, *marks):
    node = {"type": "text", "text": text}
    if marks:
        node["marks"] = list(marks)
    return node


def test_none_and_plain_strings_pass_through():
    assert adf_to_text(None) == ""
    assert adf_to_text("already text") == "already text"


def test_paragraphs_are_separated_by_a_blank_line():
    assert adf_to_text(_doc(_p(_t("First.")), _p(_t("Second.")))) == "First.\n\nSecond."


def test_inline_nodes():
    doc = _doc(
        _p(
            _t("Hi "),
            {"type": "mention", "attrs": {"id": "abc", "text": "@Asha"}},
            _t(", see "),
            _t("docs", {"type": "link", "attrs": {"href": "https://x.test/d"}}),
            {"type": "hardBreak"},
            _t("run "),
            _t("make", {"type": "code"}),
            {"type": "emoji", "attrs": {"shortName": ":fire:", "text": "🔥"}},
        )
    )
    assert adf_to_text(doc) == "Hi @Asha, see docs (https://x.test/d)\nrun `make`🔥"


def test_lists_and_nested_lists():
    def item(*content):
        return {"type": "listItem", "content": list(content)}

    doc = _doc(
        {
            "type": "orderedList",
            "attrs": {"order": 1},
            "content": [
                item(_p(_t("one"))),
                item(_p(_t("two")), {"type": "bulletList", "content": [item(_p(_t("nested")))]}),
            ],
        }
    )
    assert adf_to_text(doc) == "1. one\n2. two\n  - nested"


def test_code_block_quote_and_table():
    doc = _doc(
        {"type": "codeBlock", "content": [_t("SELECT 1;")]},
        {"type": "blockquote", "content": [_p(_t("quoted"))]},
        {
            "type": "table",
            "content": [
                {"type": "tableRow", "content": [
                    {"type": "tableHeader", "content": [_p(_t("a"))]},
                    {"type": "tableCell", "content": [_p(_t("b"))]},
                ]},
            ],
        },
    )
    assert adf_to_text(doc) == "```\nSELECT 1;\n```\n\n> quoted\n\na | b"


def test_unknown_nodes_fall_back_to_children_and_media_is_marked():
    doc = _doc(
        {"type": "someFutureNode", "content": [_t("kept")]},
        {"type": "mediaSingle", "content": [{"type": "media", "attrs": {"id": "1"}}]},
    )
    assert adf_to_text(doc) == "kept\n[attachment]"
