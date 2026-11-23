import base64
import io
import json

import pytest
from PIL import Image

from novel_quote_posters.rendering import render_cards
from novel_quote_posters.review import (
    apply_review,
    character_label,
    review_targets,
    write_review_page,
)
from novel_quote_posters.storage import load_json, save_json


def signed_test_record(run, *, quotes_only=False):
    """Simulated human test data, never used as a real sample approval."""
    form = review_targets(run, quotes_only)
    form.update(reviewer="SIMULATED TEST REVIEWER", attested=True,
                reviewed_at="2026-11-22T00:00:00+00:00")
    for row in form["quotes"] + form["cards"]:
        row["status"] = "approved"
    for row in form["cards"]:
        row["visual_checks"] = dict.fromkeys(row["visual_checks"], "passed")
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


def test_selected_review_partial_roundtrip_preserves_unselected_pending(poster_run, tmp_path):
    quotes = load_json(poster_run / "quotes.json")
    quotes[0]["selection"]["selected"] = True
    quotes[1]["selection"]["selected"] = False
    save_json(poster_run / "quotes.json", quotes)
    record = write_review_page(poster_run, tmp_path / "selected.html", quotes_only=True, selected_only=True)
    assert [q["quote_id"] for q in record["quotes"]] == [quotes[0]["quote_id"]]
    assert record["quotes"][0]["status"] == "pending" and not record["attested"]
    with pytest.raises(ValueError, match="attestation"):
        apply_review(poster_run, record)
    # A simulated signature tests partial import; this is not a real user approval.
    record.update(reviewer="SIMULATED TEST REVIEWER", attested=True, reviewed_at="2026-11-23T00:00:00+00:00")
    record["quotes"][0]["status"] = "approved"
    assert apply_review(poster_run, record)["quotes_changed"] == 1
    actual = load_json(poster_run / "quotes.json")
    assert actual[0]["review"]["status"] == "approved"
    assert actual[1]["review"]["status"] == "pending"
    actual[0]["speaker"]["explanation"] = "Changed attribution needs new context review"
    save_json(poster_run / "quotes.json", actual)
    with pytest.raises(ValueError, match="invalidated by a content change"):
        apply_review(poster_run, record)


def test_selected_review_requires_quote_scope_and_explicit_true(poster_run, tmp_path):
    page = tmp_path / "empty.html"
    with pytest.raises(ValueError, match="requires quotes-only"):
        write_review_page(poster_run, page, selected_only=True)
    quotes = load_json(poster_run / "quotes.json")
    for quote in quotes:
        quote["selection"]["selected"] = "true"  # Not an explicit Boolean selection.
    save_json(poster_run / "quotes.json", quotes)
    with pytest.raises(ValueError, match="no explicitly selected"):
        write_review_page(poster_run, page, quotes_only=True, selected_only=True)
    assert not page.exists() and not page.with_suffix(".targets.json").exists()


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


def test_character_identity_change_invalidates_prior_speaker_approval(poster_run):
    record = signed_test_record(poster_run, quotes_only=True)
    apply_review(poster_run, record)
    characters = load_json(poster_run / "characters.json")
    characters["lin_che"]["name"] = "另一个角色"
    save_json(poster_run / "characters.json", characters)
    with pytest.raises(ValueError, match="invalidated"):
        apply_review(poster_run, record)


def test_same_name_characters_are_distinguishable_to_the_human_reviewer():
    characters = {"young": {"name": "林澈", "aliases": ["林澈", "阿澈"]},
                  "elder": {"name": "林澈", "aliases": ["林澈", "林先生"]}}
    assert character_label("young", characters) == "林澈（阿澈）"
    assert character_label("elder", characters) == "林澈（林先生）"


def test_visual_views_include_baseline_mobile_and_explicit_crop_without_granting_approval(poster_run, chinese_font, tmp_path):
    characters = load_json(poster_run / "characters.json")
    characters["lin_che"].update(reference_image="test-only-1.png", traits=[
        {"kind": "design_choice", "value": "SYNTHETIC TEST BASELINE", "evidence": []}])
    save_json(poster_run / "characters.json", characters)
    render_cards(poster_run, chinese_font, draft=False)
    page = tmp_path / "comparison.html"
    targets = write_review_page(poster_run, page)
    html = page.read_text(encoding="utf-8")
    view = json.loads(html.split('const review=', 1)[1].split(';\n', 1)[0])
    row = view["cards"][0]
    assert set(row["view_hashes"]) == {"reference", "full", "mobile", "center_square_simulation"}
    assert all(value == "pending" for value in row["visual_checks"].values())
    assert targets["cards"][0]["status"] == "pending" and not targets["attested"]
    for key, size in [("full", (1080, 1440)), ("mobile", (360, 480)), ("center_square_simulation", (1080, 1080))]:
        raw = base64.b64decode(row["view_data"][key].split(',', 1)[1])
        with Image.open(io.BytesIO(raw)) as image:
            assert image.size == size
    assert row["crop_box"] == [0, 180, 1080, 1260]
    assert "非平台规则" in html and "SYNTHETIC TEST BASELINE" in html


def test_incomplete_checklist_and_changed_view_never_apply_partial_approval(poster_run, chinese_font):
    render_cards(poster_run, chinese_font, draft=False)
    record = signed_test_record(poster_run)
    original = (poster_run / "quotes.json").read_bytes()
    record["cards"][0]["visual_checks"]["text_exact"] = "pending"
    with pytest.raises(ValueError, match="checklist"):
        apply_review(poster_run, record)
    assert (poster_run / "quotes.json").read_bytes() == original
    record["cards"][0]["visual_checks"]["text_exact"] = "passed"
    record["cards"][0]["view_hashes"]["mobile"] = "altered"
    with pytest.raises(ValueError, match="views"):
        apply_review(poster_run, record)
    assert (poster_run / "quotes.json").read_bytes() == original


def test_damaged_mobile_preview_and_old_review_schema_are_rejected(poster_run, chinese_font):
    render_cards(poster_run, chinese_font)
    form = review_targets(poster_run)
    form["schema_version"] = 1
    with pytest.raises(ValueError, match="regenerate"):
        apply_review(poster_run, form)
    card = load_json(poster_run / "cards.json")[0]
    image = poster_run / card["poster"]
    image.with_name(image.stem + "-preview.png").write_bytes(b"damaged")
    with pytest.raises(ValueError, match="mobile preview"):
        review_targets(poster_run)
