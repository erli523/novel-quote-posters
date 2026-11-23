from novel_quote_posters.bootstrap import initialize_run
from novel_quote_posters.images import attach_image, register_image
from novel_quote_posters.ingest import index_text
from novel_quote_posters.rendering import render_cards
from novel_quote_posters.source_quality import source_observations
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


def test_source_observations_warn_without_repairing_or_certifying_text():
    text = '第一章 测试\n甲说：“完整。”\n乙说：“缺□字\ufffd'
    book = index_text(text, "self-authored-test", "自写样本")
    result = source_observations(book)
    assert result["square_glyphs"] == result["replacement_characters"] == 1
    assert result["balanced_outer_pairs_observed"] == 1
    assert result["quote_delimiter_issues"] == {"unclosed_quote": 1}
    assert len(result["warnings"]) == 2 and not result["source_modified"]
    assert result["candidate_recall"] == "not_measured"
    assert result["transcription_accuracy"] == "not_verified"
    assert book["text"] == text


def test_zero_delimiter_warnings_do_not_certify_semantics():
    book = index_text('第一章 测试\n甲说：“这句话仍需核对人物。”', "self-authored-test", "自写样本")
    result = source_observations(book)
    assert result["warnings"] == [] and result["quote_delimiter_issue_count"] == 0
    assert result["candidate_recall"] == "not_measured" and result["transcription_accuracy"] == "not_verified"


def test_complete_index_exposes_source_warnings_without_writing_or_using_budget(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text('第一章 测试\n甲说：“完整。”\n乙说：“缺□字', encoding="utf-8")
    run = tmp_path / "run"
    initialize_run(source, run, image_limit=0)
    # Missing an auxiliary old report cannot be mistaken for no delimiter issues.
    (run / "candidate-extraction.json").unlink()
    names = ["source.txt", "book.json", "quotes.json", "chunks.json"]
    before = {name: digest_file(run / name) for name in names}
    result = run_status(run)
    assert result["index_ranges_complete"]
    assert result["source_observations"]["quote_delimiter_issues"] == {"unclosed_quote": 1}
    assert result["source_observations"]["square_glyphs"] == 1
    assert result["budget"]["reserved_calls"] == 0
    assert result["overall_acceptance"] == "not_inferred_from_progress"
    assert before == {name: digest_file(run / name) for name in names}
