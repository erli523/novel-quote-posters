from novel_quote_posters.images import attach_image, register_image
from novel_quote_posters.rendering import render_cards
from novel_quote_posters.status import run_status
from novel_quote_posters.storage import digest_file, load_json, save_json


def test_status_distinguishes_awaiting_draft_and_stale_without_claiming_acceptance(poster_run, chinese_font):
    save_json(poster_run / "chunks.json", [])
    status = run_status(poster_run)
    assert status["card_counts"] == {"awaiting_render": 2}
    receipt = {"source_kind": "test_fixture", "character_id": "lin_che",
               "image_sha256": digest_file(poster_run / "test-only-1.png"), "source_note": "test only"}
    asset = register_image(poster_run, poster_run / "test-only-1.png", receipt)
    attach_image(poster_run, asset["asset_id"], "card001")
    render_cards(poster_run, chinese_font)
    status = run_status(poster_run)
    assert status["card_counts"] == {"draft_ready": 2}
    assert status["overall_acceptance"] == "not_inferred_from_progress"
    cards = load_json(poster_run / "cards.json")
    cards[0]["layout_overrides"] = {"bg": "#000000"}
    save_json(poster_run / "cards.json", cards)
    assert run_status(poster_run)["card_counts"]["invalidated"] == 1


def test_missing_run_status_does_not_create_a_directory(tmp_path):
    path = tmp_path / "not-created"
    assert run_status(path)["ingest"] == "not_started"
    assert not path.exists()
