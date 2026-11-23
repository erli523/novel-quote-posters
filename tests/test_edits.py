import copy

import pytest

from novel_quote_posters import edits
from novel_quote_posters.documents import commit_documents, recover_documents
from novel_quote_posters.export import export_bundle, export_status
from novel_quote_posters.rendering import render_cards
from novel_quote_posters.storage import digest_file, load_json, save_json
from novel_quote_posters.workflow import Ledger


def request(run, operations):
    form = edits.edit_targets(run)
    form.pop("available")
    return {**form, "editor": "SIMULATED APPLICATION TEST", "reason": "controlled automated fixture edit",
            "operations": operations}


def test_display_edit_preserves_original_background_and_old_output_and_replays(poster_run, chinese_font):
    render_cards(poster_run, chinese_font)
    before = load_json(poster_run / "cards.json")
    old = poster_run / before[0]["poster"]
    original_hash, background_hash = digest_file(old), digest_file(poster_run / before[0]["background"])
    quote = load_json(poster_run / "quotes.json")[0]
    form = request(poster_run, [{"type": "set-display-text", "quote_id": quote["quote_id"],
                              "display_text": "山路还在。", "text_mode": "rewrite"}])
    with pytest.raises(ValueError, match="rewrite"):
        edits.apply_edits(poster_run, form)
    result = edits.apply_edits(poster_run, form, allow_rewrite=True)
    updated = load_json(poster_run / "quotes.json")[0]
    assert updated["quote_exact"] == quote["quote_exact"] and updated["source"] == quote["source"]
    assert updated["display_text"] == "山路还在。" and updated["review"]["status"] == "pending"
    assert "poster" not in load_json(poster_run / "cards.json")[0]
    assert digest_file(old) == original_hash
    assert digest_file(poster_run / before[0]["background"]) == background_hash
    assert edits.apply_edits(poster_run, form, allow_rewrite=True)["reused"]
    rerender = render_cards(poster_run, chinese_font)
    assert rerender["rendered"] == 1 and rerender["reused"] == 1
    assert result["image_tool_calls"] == rerender["image_tool_calls"] == 0
    with Ledger(poster_run / ".state/ledger.sqlite") as ledger:
        assert not ledger.budget()["calls"]


def test_entire_edit_batch_is_validated_and_stale_snapshot_refuses_overwrite(poster_run):
    quote = load_json(poster_run / "quotes.json")[0]
    form = request(poster_run, [
        {"type": "set-display-text", "quote_id": quote["quote_id"], "display_text": "换字。", "text_mode": "rewrite"},
        {"type": "set-style", "card_id": "does_not_exist", "style_id": "mist_cinema"}])
    before = (poster_run / "quotes.json").read_bytes()
    with pytest.raises(ValueError, match="unknown edit target"):
        edits.apply_edits(poster_run, form, allow_rewrite=True)
    assert (poster_run / "quotes.json").read_bytes() == before
    form["operations"] = form["operations"][:1]
    quotes = load_json(poster_run / "quotes.json")
    quotes[0]["selection"]["reason"] = "a later independent application change"
    save_json(poster_run / "quotes.json", quotes)
    with pytest.raises(ValueError, match="fresh snapshot"):
        edits.apply_edits(poster_run, form, allow_rewrite=True)


def test_character_rebinding_drops_old_imagery_pointers_but_keeps_assets(poster_run, chinese_font):
    render_cards(poster_run, chinese_font)
    old = load_json(poster_run / "cards.json")[0]
    characters = load_json(poster_run / "characters.json")
    characters["other"] = {"character_id": "other", "name": "另一个人", "traits": []}
    save_json(poster_run / "characters.json", characters)
    quotes = load_json(poster_run / "quotes.json")
    quotes[1]["speaker"]["character_id"] = "other"  # Controlled semantic correction, not a human approval.
    save_json(poster_run / "quotes.json", quotes)
    form = request(poster_run, [{"type": "bind-card", "card_id": "card001", "quote_id": quotes[1]["quote_id"]}])
    result = edits.apply_edits(poster_run, form)
    new = load_json(poster_run / "cards.json")[0]
    assert new["character_id"] == "other" and new["image_kind"] == "pending"
    assert not {"poster", "background", "image_asset_id"} & set(new)
    assert (poster_run / old["poster"]).exists() and (poster_run / old["background"]).exists()
    assert result["impacts"][0]["character_changed"] and result["image_tool_calls"] == 0


def test_reordering_invalidates_export_and_reuses_every_single_card(poster_run, chinese_font, tmp_path):
    render_cards(poster_run, chinese_font)
    export_bundle(poster_run, tmp_path / "old-export", draft=True)
    form = request(poster_run, [{"type": "reorder-cards", "card_ids": ["card002", "card001"]}])
    result = edits.apply_edits(poster_run, form)
    assert result["impacts"][0]["album_order_changed"]
    assert export_status(poster_run)["state"] == "invalidated"
    assert render_cards(poster_run, chinese_font)["reused"] == 2


def test_commit_compares_expected_documents_inside_write_lock(poster_run):
    before = {name: load_json(poster_run / name) for name in edits.DOCUMENTS}
    changed = copy.deepcopy(before["quotes.json"])
    changed[0]["selection"]["reason"] = "concurrent update"
    save_json(poster_run / "quotes.json", changed)
    with pytest.raises(ValueError, match="changed since edit snapshot"):
        commit_documents(poster_run, {"cards.json": []}, reason="test stale edit", expected=before)
    assert load_json(poster_run / "cards.json") == before["cards.json"]
    save_json(poster_run / "quotes.json", before["quotes.json"])
    book = load_json(poster_run / "book.json")
    save_json(poster_run / "book.json", {**book, "title": "a later title"})
    with pytest.raises(ValueError, match="changed since edit snapshot"):
        commit_documents(poster_run, {"cards.json": []}, reason="test stale title",
                         expected={**before, "book.json": book})
    assert load_json(poster_run / "cards.json") == before["cards.json"]


def test_edit_interruption_after_commit_recovers_once_without_overwriting_later_changes(poster_run, monkeypatch):
    form = request(poster_run, [{"type": "set-style", "card_id": "card001", "style_id": "mist_cinema"}])
    actual = edits.commit_documents

    def interrupt(*args, **kwargs):
        actual(*args, **kwargs)
        raise OSError("injected loss after durable edit commit")

    monkeypatch.setattr(edits, "commit_documents", interrupt)
    with pytest.raises(OSError, match="injected"):
        edits.apply_edits(poster_run, form)
    monkeypatch.setattr(edits, "commit_documents", actual)
    recover_documents(poster_run)
    assert edits.apply_edits(poster_run, form)["reused"]
    later = request(poster_run, [{"type": "set-style", "card_id": "card001", "style_id": "dark_stage"}])
    edits.apply_edits(poster_run, later)
    with pytest.raises(ValueError, match="cannot overwrite later changes"):
        edits.apply_edits(poster_run, form)
    assert load_json(poster_run / "cards.json")[0]["style_id"] == "dark_stage"


def test_noop_batch_replay_never_creates_duplicate_transactions(poster_run):
    form = request(poster_run, [{"type": "reorder-cards", "card_ids": ["card002", "card001"]},
                               {"type": "reorder-cards", "card_ids": ["card001", "card002"]}])
    assert edits.apply_edits(poster_run, form)["transaction"] is None
    assert edits.apply_edits(poster_run, form)["reused"]
    assert not list((poster_run / ".state/transactions").glob("*.json"))


def test_interrupted_plan_is_not_silently_replaced_after_history_damage(poster_run, monkeypatch):
    form = request(poster_run, [{"type": "set-style", "card_id": "card001", "style_id": "dark_stage"}])
    original = edits.commit_documents

    def before_commit(*args, **kwargs):
        raise OSError("before commit")

    monkeypatch.setattr(edits, "commit_documents", before_commit)
    with pytest.raises(OSError, match="before commit"):
        edits.apply_edits(poster_run, form)
    history = next((poster_run / ".state/edits").glob("*.json"))
    damaged = load_json(history)
    damaged["result"]["image_tool_calls"] = 99
    save_json(history, damaged)
    monkeypatch.setattr(edits, "commit_documents", original)
    with pytest.raises(ValueError, match="plan history differs"):
        edits.apply_edits(poster_run, form)
    assert load_json(history) == damaged
    assert load_json(poster_run / "cards.json")[0]["style_id"] == "archive_paper"


@pytest.mark.parametrize("operation", [
    {"type": "replace-source", "text": "tamper"},
    {"type": "reorder-cards", "card_ids": ["card001", "card001"]},
    {"type": "bind-card", "card_id": "card001", "quote_id": "unknown"},
    {"type": "set-style", "card_id": "card001", "style_id": "unknown", "review": "approved"},
])
def test_unsupported_or_incomplete_edits_leave_documents_unchanged(poster_run, operation):
    before = {name: (poster_run / name).read_bytes() for name in edits.DOCUMENTS}
    with pytest.raises(ValueError):
        edits.apply_edits(poster_run, request(poster_run, [operation]))
    assert before == {name: (poster_run / name).read_bytes() for name in edits.DOCUMENTS}
