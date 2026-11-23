import copy

import pytest

from novel_quote_posters import images
from novel_quote_posters.contracts import apply_contract, contract_template
from novel_quote_posters.documents import commit_documents, recover_documents
from novel_quote_posters.rendering import render_cards
from novel_quote_posters.storage import digest_file, load_json, save_json


def selected_quotes(run, count):
    quotes = load_json(run / "quotes.json")
    for index, quote in enumerate(quotes):
        quote["selection"]["selected"] = index < count
    save_json(run / "quotes.json", quotes)


def test_fill_shortfall_preserves_existing_card_asset_order_and_review_and_is_idempotent(poster_run, chinese_font):
    render_cards(poster_run, chinese_font)
    original = load_json(poster_run / "cards.json")
    first = copy.deepcopy(original[0])
    first["review"] = {"status": "pending", "note": "SIMULATED KEEP THIS REVIEW NOTE"}
    save_json(poster_run / "cards.json", [first])
    old_hash = digest_file(poster_run / first["poster"])
    selected_quotes(poster_run, 2)
    result = images.prepare_cards(poster_run, append=True)
    cards = load_json(poster_run / "cards.json")
    assert cards[0] == first and cards[1]["card_id"] == "card003"  # card002 exists in tracked render history.
    assert len(cards) == result["prepared"] == 2 and result["added"] == 1
    assert digest_file(poster_run / first["poster"]) == old_hash
    transactions = list((poster_run / ".state/transactions").glob("*.json"))
    again = images.prepare_cards(poster_run, append=True)
    assert again["added"] == 0 and again["reused"] and again["transaction"] is None
    assert list((poster_run / ".state/transactions").glob("*.json")) == transactions
    assert images.budget_summary(poster_run)["reserved_calls"] == 0


def test_new_candidates_after_shortfall_are_appended_without_replacing_first_selection(poster_run):
    (poster_run / "cards.json").unlink()
    selected_quotes(poster_run, 1)
    initial = images.prepare_cards(poster_run)
    assert initial["prepared"] == 1 and initial["shortfall"] == 1
    first = load_json(poster_run / "cards.json")[0]
    selected_quotes(poster_run, 2)
    result = images.prepare_cards(poster_run, append=True)
    assert result["added"] == 1 and result["shortfall"] == 0
    cards = load_json(poster_run / "cards.json")
    assert cards[0] == first and cards[1]["card_id"] == "card002"


def test_append_recovers_partial_counter_and_cards_commit_without_duplicate_ids(poster_run, monkeypatch):
    first = load_json(poster_run / "cards.json")[0]
    save_json(poster_run / "cards.json", [first])
    selected_quotes(poster_run, 2)
    actual = images.commit_documents

    def interrupted(*args, **kwargs):
        actual(*args, **kwargs)
        raise OSError("injected return loss after atomic append")

    monkeypatch.setattr(images, "commit_documents", interrupted)
    with pytest.raises(OSError, match="injected"):
        images.prepare_cards(poster_run, append=True)
    monkeypatch.setattr(images, "commit_documents", actual)
    recover_documents(poster_run)
    result = images.prepare_cards(poster_run, append=True)
    assert result["reused"] and result["added"] == 0
    assert [c["card_id"] for c in load_json(poster_run / "cards.json")] == ["card001", "card002"]
    assert load_json(poster_run / "card-sequence.json")["last_number"] == 2


def test_sequence_and_new_cards_do_not_commit_over_a_concurrent_album_edit(poster_run, monkeypatch):
    first = load_json(poster_run / "cards.json")[0]
    save_json(poster_run / "cards.json", [first])
    selected_quotes(poster_run, 2)
    actual = images.commit_documents
    concurrent = [{**first, "style_id": "dark_stage", "review": {"status": "pending", "note": "SIMULATED CONCURRENT EDIT"}}]

    def race(run, values, **kwargs):
        save_json(run / "cards.json", concurrent)
        return actual(run, values, **kwargs)

    monkeypatch.setattr(images, "commit_documents", race)
    with pytest.raises(ValueError, match="changed since edit snapshot"):
        images.prepare_cards(poster_run, append=True)
    assert load_json(poster_run / "cards.json") == concurrent
    assert not (poster_run / "card-sequence.json").exists()


def test_append_never_recycles_tracked_removed_ids_or_deletes_excess_cards(poster_run):
    original = load_json(poster_run / "cards.json")
    commit_documents(poster_run, {"cards.json": original[:1]}, reason="SIMULATED explicit tracked removal")
    selected_quotes(poster_run, 2)
    images.prepare_cards(poster_run, append=True)
    assert load_json(poster_run / "cards.json")[1]["card_id"] == "card003"
    form = contract_template(poster_run)
    form.update(editor="SIMULATED TEST", reason="explicit target reduction")
    form["definition"]["target_count"] = 1
    apply_contract(poster_run, form)
    before = (poster_run / "cards.json").read_bytes()
    with pytest.raises(ValueError, match="never deletes"):
        images.prepare_cards(poster_run, append=True)
    assert (poster_run / "cards.json").read_bytes() == before


def test_missing_card_pointer_and_corrupt_sequence_do_not_silently_create_new_album(poster_run):
    selected_quotes(poster_run, 2)
    save_json(poster_run / "card-sequence.json", {"schema_version": 1, "last_number": 0})
    with pytest.raises(ValueError, match="sequence damaged"):
        images.prepare_cards(poster_run, append=True)
    save_json(poster_run / "card-sequence.json", {"schema_version": 1, "last_number": 2})
    (poster_run / "cards.json").unlink()
    with pytest.raises(ValueError, match="cards.json is missing"):
        images.prepare_cards(poster_run, append=True)
    assert not (poster_run / "cards.json").exists()
