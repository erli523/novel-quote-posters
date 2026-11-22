import pytest

from novel_quote_posters import rendering
from novel_quote_posters.export import export_bundle
from novel_quote_posters.storage import digest_file, load_json, save_json
from novel_quote_posters.workflow import Ledger


def test_repeat_render_reuses_exact_assets_and_order_change_does_not_rerender(poster_run, chinese_font):
    result = rendering.render_cards(poster_run, chinese_font)
    assert result["rendered"] == 2
    cards = load_json(poster_run / "cards.json")
    original = {c["card_id"]: (c["poster"], digest_file(poster_run / c["poster"])) for c in cards}
    result = rendering.render_cards(poster_run, chinese_font)
    assert result["rendered"] == 0 and result["reused"] == 2
    save_json(poster_run / "cards.json", list(reversed(cards)))
    result = rendering.render_cards(poster_run, chinese_font)
    assert result["reused"] == 2 and result["image_tool_calls"] == 0
    for card in load_json(poster_run / "cards.json"):
        assert original[card["card_id"]] == (card["poster"], digest_file(poster_run / card["poster"]))


def test_text_edit_rerenders_one_card_preserving_original_and_background(poster_run, chinese_font):
    rendering.render_cards(poster_run, chinese_font)
    cards = load_json(poster_run / "cards.json")
    old_image = poster_run / cards[0]["poster"]
    old_hash = digest_file(old_image)
    background_hash = digest_file(poster_run / cards[0]["background"])
    quotes = load_json(poster_run / "quotes.json")
    quotes[0].update(display_text="山路还在。", text_mode="rewrite")
    save_json(poster_run / "quotes.json", quotes)
    result = rendering.render_cards(poster_run, chinese_font)
    assert result["rendered"] == 1 and result["reused"] == 1
    assert old_hash == digest_file(old_image)
    assert background_hash == digest_file(poster_run / cards[0]["background"])
    assert result["image_tool_calls"] == 0


def test_character_revision_invalidates_every_related_card_even_if_one_is_rendered(poster_run, chinese_font, tmp_path):
    rendering.render_cards(poster_run, chinese_font)
    characters = load_json(poster_run / "characters.json")
    characters["lin_che"]["traits"].append({"kind": "design_choice", "value": "new coat"})
    save_json(poster_run / "characters.json", characters)
    rendering.render_cards(poster_run, chinese_font, card_id="card001")
    with Ledger(poster_run / ".state/ledger.sqlite") as ledger:
        assert ledger.valid("poster:card001")
        assert not ledger.valid("poster:card002")
    with pytest.raises(ValueError, match="inputs changed"):
        export_bundle(poster_run, tmp_path / "stale-export", draft=True)


def test_completed_render_survives_crash_before_card_commit(poster_run, chinese_font, monkeypatch):
    actual = rendering.commit_documents

    def crash(*args, **kwargs):
        raise OSError("injected loss after render before card pointer update")

    monkeypatch.setattr(rendering, "commit_documents", crash)
    with pytest.raises(OSError, match="injected"):
        rendering.render_cards(poster_run, chinese_font, card_id="card001")
    monkeypatch.setattr(rendering, "commit_documents", actual)
    result = rendering.render_cards(poster_run, chinese_font, card_id="card001")
    assert result["rendered"] == 0 and result["reused"] == 1


def test_incomplete_or_corrupted_render_is_versioned_not_overwritten(poster_run, chinese_font):
    rendering.render_cards(poster_run, chinese_font, card_id="card001")
    card = load_json(poster_run / "cards.json")[0]
    old = poster_run / card["poster"]
    old.write_bytes(b"damaged image")
    result = rendering.render_cards(poster_run, chinese_font, card_id="card001")
    assert result["rendered"] == 1 and result["cards"][0]["poster"] != card["poster"]
    assert old.read_bytes() == b"damaged image"
