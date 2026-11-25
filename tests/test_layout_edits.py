import json
import zipfile

import pytest
from PIL import Image

from novel_quote_posters import edits
from novel_quote_posters.export import export_bundle, export_status
from novel_quote_posters.layout import compose, resolve_style
from novel_quote_posters.rendering import render_cards
from novel_quote_posters.storage import digest_file, load_json
from novel_quote_posters.workflow import Ledger


def plan(run, overrides):
    return {**edits.edit_targets(run), "editor": "SIMULATED APPLICATION TEST",
            "reason": "Controlled layout fixture; no human approval",
            "operations": [{"type": "set-layout", "card_id": "card001", "overrides": overrides}]}


def test_layout_edit_reuses_image_invalidates_one_card_replays_and_resets(poster_run, chinese_font, tmp_path):
    render_cards(poster_run, chinese_font)
    before = load_json(poster_run / "cards.json")
    quote_bytes = (poster_run / "quotes.json").read_bytes()
    character_bytes = (poster_run / "characters.json").read_bytes()
    original = poster_run / before[0]["poster"]
    original_hash = digest_file(original)
    export_bundle(poster_run, tmp_path / "before", draft=True)
    request = plan(poster_run, {"image_fit": "contain", "image_anchor": [.5, .1], "max_crop": .1})
    result = edits.apply_edits(poster_run, request)
    cards = load_json(poster_run / "cards.json")
    assert "poster" not in cards[0] and cards[1] == before[1]
    assert cards[0]["background"] == before[0]["background"]
    assert result["impacts"][0]["invalidated_cards"] == ["card001"]
    assert result["impacts"][0]["render_validation_required"]
    assert export_status(poster_run)["state"] == "invalidated"
    assert edits.apply_edits(poster_run, request)["reused"]
    rendered = render_cards(poster_run, chinese_font)
    assert rendered["rendered"] == 1 and rendered["reused"] == 1
    assert digest_file(original) == original_hash
    assert (poster_run / "quotes.json").read_bytes() == quote_bytes
    assert (poster_run / "characters.json").read_bytes() == character_bytes
    export_bundle(poster_run, tmp_path / "changed", draft=True)
    with zipfile.ZipFile(tmp_path / "changed/draft-review.zip") as archive:
        packaged = json.loads(archive.read("cards.json"))[0]
        assert packaged["layout_overrides"] == request["operations"][0]["overrides"]
        report = json.loads(archive.read(packaged["layout_report"]))
        assert report["style"]["image_fit"] == "contain"
        assert report["image_fit"]["applied"] == "contain"
    edits.apply_edits(poster_run, plan(poster_run, {}))
    assert "layout_overrides" not in load_json(poster_run / "cards.json")[0]
    restored = render_cards(poster_run, chinese_font)
    assert restored["reused"] == 2
    assert load_json(poster_run / "cards.json")[0]["poster"] == before[0]["poster"]
    with pytest.raises(ValueError, match="fresh request"):
        edits.apply_edits(poster_run, request)
    with Ledger(poster_run / ".state/ledger.sqlite") as ledger:
        assert not ledger.budget()["calls"]


def test_equivalent_color_is_canonical_noop_and_preserves_current_render(poster_run, chinese_font):
    edits.apply_edits(poster_run, plan(poster_run, {"bg": "white"}))
    render_cards(poster_run, chinese_font)
    before = (poster_run / "cards.json").read_bytes()
    result = edits.apply_edits(poster_run, plan(poster_run, {"bg": "#FFFFFF"}))
    assert result["transaction"] is None and result["impacts"] == []
    assert (poster_run / "cards.json").read_bytes() == before


def test_hidden_chapter_survives_render_resume_and_export_without_erasing_source(
        poster_run, chinese_font, tmp_path):
    quotes_before = (poster_run / "quotes.json").read_bytes()
    edits.apply_edits(poster_run, plan(poster_run, {"show_chapter": False}))
    render_cards(poster_run, chinese_font)
    assert render_cards(poster_run, chinese_font)["reused"] == 2
    export_bundle(poster_run, tmp_path / "hidden", draft=True)
    with zipfile.ZipFile(tmp_path / "hidden/draft-review.zip") as archive:
        card = json.loads(archive.read("cards.json"))[0]
        report = json.loads(archive.read(card["layout_report"]))
        assert card["layout_overrides"]["show_chapter"] is False
        assert report["style"]["show_chapter"] is False
        assert "header" not in report["auxiliary_text"]
    assert (poster_run / "quotes.json").read_bytes() == quotes_before
    assert load_json(poster_run / "quotes.json")[0]["source"]["chapter"]
    edits.apply_edits(poster_run, plan(poster_run, {"show_chapter": True}))
    render_cards(poster_run, chinese_font)
    card = load_json(poster_run / "cards.json")[0]
    report = load_json((poster_run / card["poster"]).with_suffix(".layout.json"))
    assert report["auxiliary_text"]["header"]["text"]
    with Ledger(poster_run / ".state/ledger.sqlite") as ledger:
        assert not ledger.budget()["calls"]


@pytest.mark.parametrize("overrides", [None, [], {"font_path": "private"},
    {"image": [-.1, 0, 1, 1]}, {"image": [0, 0, 2, 1]}, {"image": [0, 0, 0, 1]},
    {"image": [0, 0, float("nan"), 1]}, {"panel": [False, 0, 1, 1]},
    {"text": [.01, .6, .9, .8]}, {"text": [.1, .14, .9, .3]},
    {"text": [.1, .6, .9, .9]}, {"panel": [0, .7, 1, 1]},
    {"image_fit": []}, {"max_crop": 1}, {"max_crop": True},
    {"image_anchor": [.5, 2]}, {"image_anchor": [float("inf"), .5]},
    {"bg": "rgba(0,0,0,0.5)"}, {"fg": "#ffffff", "bg": "#ffffff"},
    {"show_chapter": "false"}, {"show_chapter": 0}, {"show_chapter": None}])
def test_invalid_layout_is_rejected_without_any_document_change(poster_run, overrides):
    before = {name: (poster_run / name).read_bytes() for name in edits.DOCUMENTS}
    with pytest.raises(ValueError):
        edits.apply_edits(poster_run, plan(poster_run, overrides))
    assert before == {name: (poster_run / name).read_bytes() for name in edits.DOCUMENTS}
    assert not list((poster_run / ".state/edits").glob("*.json"))


def test_valid_layout_cannot_partially_commit_before_later_invalid_operation(poster_run):
    request = plan(poster_run, {"image_fit": "contain"})
    request["operations"].append({"type": "set-layout", "card_id": "card002", "overrides": {"max_crop": -1}})
    before = (poster_run / "cards.json").read_bytes()
    with pytest.raises(ValueError):
        edits.apply_edits(poster_run, request)
    assert (poster_run / "cards.json").read_bytes() == before


@pytest.mark.parametrize("style", ["mist_cinema", "ink_whitespace", "dark_stage"])
def test_unrelated_override_keeps_long_sentence_adaptation(tmp_path, chinese_font, style):
    text = "路"*100
    assert resolve_style(style, text, {"image_anchor": [.5, .1]})["text"] == resolve_style(style, text)["text"]
    source = tmp_path / "synthetic.png"
    Image.new("RGB", (600, 900), "gray").save(source)
    report = compose(source, tmp_path / "poster.png", text=text, character="林澈", title="雾山来信",
                     chapter="第一章 山路", font_path=chinese_font, style_id=style,
                     overrides={"image_anchor": [.5, .1]})
    assert report["font_size"] >= 36 and "".join(l["text"] for l in report["lines"]) == text


def test_style_switch_clears_layout_override(poster_run):
    edits.apply_edits(poster_run, plan(poster_run, {"image_fit": "contain"}))
    request = plan(poster_run, {})
    request["operations"] = [{"type": "set-style", "card_id": "card001", "style_id": "dark_stage"}]
    edits.apply_edits(poster_run, request)
    assert "layout_overrides" not in load_json(poster_run / "cards.json")[0]


def test_unapplied_request_is_stale_after_identical_content_is_restored(poster_run, chinese_font):
    # Stabilize the pending-review reason so the second cycle restores every byte.
    edits.apply_edits(poster_run, plan(poster_run, {"image_fit": "contain"}))
    edits.apply_edits(poster_run, plan(poster_run, {}))
    render_cards(poster_run, chinese_font)
    original = (poster_run / "cards.json").read_bytes()
    old = plan(poster_run, {"image_anchor": [.5, .1]})
    edits.apply_edits(poster_run, plan(poster_run, {"image_fit": "contain"}))
    edits.apply_edits(poster_run, plan(poster_run, {}))
    render_cards(poster_run, chinese_font)
    assert (poster_run / "cards.json").read_bytes() == original
    with pytest.raises(ValueError, match="fresh snapshot"):
        edits.apply_edits(poster_run, old)
    assert (poster_run / "cards.json").read_bytes() == original


def test_document_revision_is_rechecked_inside_commit_lock(poster_run, monkeypatch):
    request = plan(poster_run, {"image_fit": "contain"})
    before = (poster_run / "cards.json").read_bytes()
    actual = edits.commit_documents

    def concurrent_update(run, changed, **options):
        actual(run, {"album-review.json": {"status": "pending"}}, reason="simulated concurrent document commit")
        return actual(run, changed, **options)

    monkeypatch.setattr(edits, "commit_documents", concurrent_update)
    with pytest.raises(ValueError, match="fresh request"):
        edits.apply_edits(poster_run, request)
    assert (poster_run / "cards.json").read_bytes() == before


def test_old_edit_policy_without_document_revision_requires_fresh_request(poster_run):
    request = plan(poster_run, {"image_fit": "contain"})
    request.pop("expected_document_head")
    before = (poster_run / "cards.json").read_bytes()
    with pytest.raises(ValueError, match="current document revision"):
        edits.apply_edits(poster_run, request)
    assert (poster_run / "cards.json").read_bytes() == before
