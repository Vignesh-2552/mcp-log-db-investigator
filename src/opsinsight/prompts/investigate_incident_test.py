from opsinsight.prompts.investigate_incident import investigate_incident


def test_defaults_render_ticket_and_window():
    text = investigate_incident("Checkout failed for user 4417 around 14:30 IST")
    assert "Checkout failed for user 4417 around 14:30 IST" in text
    assert "Time window: `24h`" in text
    assert "`db_search_by_identifier`" in text
    assert "`trace_request`" in text
    assert "`db_id_type`" in text


def test_store_is_named_in_the_resolve_step():
    text = investigate_incident("Orders stuck", time_window="2h", store_or_domain=" olallawines.com ")
    assert "Time window: `2h`" in text
    assert "`db_resolve_store` with `olallawines.com`" in text
    assert "ambiguous: true" in text


def test_without_store_the_resolve_step_is_conditional():
    text = investigate_incident("Orders stuck")
    assert "If the ticket names a store or domain" in text


def test_report_structure_is_requested():
    text = investigate_incident("x")
    for section in ("**Summary**", "**Timeline**", "**Verified facts**", "**unverified hypotheses**",
                    "**Root cause**", "**customer-facing reply**"):
        assert section in text
