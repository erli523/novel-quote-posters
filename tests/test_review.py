import pytest

from novel_quote_posters.rendering import render_cards
from novel_quote_posters.review import apply_review, review_targets, write_review_page
from novel_quote_posters.storage import load_json, save_json


def signed_test_record(run, *, quotes_only=False):
    """Simulated human test data, never used as a real sample approval."""
    form = review_targets(run, quotes_only)
    form.update(reviewer="SIMULATED TEST REVIEWER", attested=True,
                reviewed_at="2026-11-22T00:00:00+00:00")
    for row in form["quotes"] + form["cards"]:
        row["status"] = "approved"
    return form


def test_review_template_never_grants_approval_and_escapes_novel_html(poster_run, tmp_path):
    quotes = load_json(poster_run / "quotes.json")
    quotes[0].update(display_text="</script><script>alert(1)</script>", text_mode="rewrite")
    save_json(poster_run / "quotes.json", quotes)
    page = tmp_path / "review.html"
    form = write_review_page(poster_run, page, quotes_only=True)
    assert not form["attested"] and form["reviewer"] == ""
    assert all(row["status"] == "pending" for row in form["quotes"])
    html = page.read_text(encoding="utf-8")
    assert "</script><script>alert(1)</script>" not in html
    assert "\\u003c/script>" in html
    assert 'fetch(' not in html and 'XMLHttpRequest' not in html
    with pytest.raises(ValueError, match="attestation"):
        apply_review(poster_run, form)


def test_review_roundtrip_is_idempotent_and_new_text_invalidates_old_form(poster_run):
    record = signed_test_record(poster_run, quotes_only=True)
    result = apply_review(poster_run, record)
    assert result["quotes_changed"] == 2
    assert apply_review(poster_run, record)["quotes_changed"] == 0
    quotes = load_json(poster_run / "quotes.json")
    quotes[0].update(display_text="路仍在。", text_mode="rewrite")
    save_json(poster_run / "quotes.json", quotes)
    with pytest.raises(ValueError, match="invalidated"):
        apply_review(poster_run, record)


def test_whole_record_is_validated_before_any_decision_is_applied(poster_run, chinese_font):
    render_cards(poster_run, chinese_font, draft=False)
    record = signed_test_record(poster_run)
    record["cards"][1]["fingerprint"] = "stale"
    before = (poster_run / "quotes.json").read_bytes()
    with pytest.raises(ValueError, match="invalidated"):
        apply_review(poster_run, record)
    assert (poster_run / "quotes.json").read_bytes() == before


def test_draft_visual_approval_and_ai_attestation_are_refused(poster_run, chinese_font):
    render_cards(poster_run, chinese_font)
    record = signed_test_record(poster_run)
    with pytest.raises(ValueError, match="clean candidate"):
        apply_review(poster_run, record)
    record["reviewer_type"] = "AI"
    with pytest.raises(ValueError, match="human attestation"):
        apply_review(poster_run, record)


def test_clean_candidates_accept_test_human_records_bound_to_both_assets(poster_run, chinese_font):
    render_cards(poster_run, chinese_font, draft=False)
    record = signed_test_record(poster_run)
    result = apply_review(poster_run, record)
    assert result["cards_changed"] == 2
    assert all(c["review"]["status"] == "approved" for c in load_json(poster_run / "cards.json"))
    assert apply_review(poster_run, record)["cards_changed"] == 0


@pytest.mark.parametrize("time", ["not a date", "2026-11-22T12:00:00", None])
def test_review_requires_a_real_timestamp_with_timezone(poster_run, time):
    record = signed_test_record(poster_run, quotes_only=True)
    record["reviewed_at"] = time
    with pytest.raises(ValueError, match="review"):
        apply_review(poster_run, record)
