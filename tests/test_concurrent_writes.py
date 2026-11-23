import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from novel_quote_posters import analysis, images, rendering
from novel_quote_posters.storage import digest_file, load_json, save_json, sha256
from novel_quote_posters.workflow import Ledger


def proposal_for(run, path):
    quotes = load_json(run / "quotes.json")
    characters = load_json(run / "characters.json")
    # Semantic proposals do not own operational image links or display edits.
    for character in characters.values():
        for key in ["reference_image", "reference_asset_id", "visual_anchor_version"]:
            character.pop(key, None)
        character["traits"].append({"kind": "design_choice", "value": "SIMULATED NEW DESIGN NOTE", "evidence": []})
    proposal = {"source_hash": load_json(run / "book.json")["source_hash"],
                "processed_chunks": [], "characters": characters, "quotes": [
                    {"quote_exact": q["quote_exact"], "start": q["source"]["start"],
                     "quote_type": q["quote_type"], "speaker": q["speaker"], "selection": q["selection"]} for q in quotes]}
    save_json(path, proposal)
    return proposal


def fixture_receipt(run, filename="test-only-1.png", note="SIMULATED FIXTURE"):
    return {"source_kind": "test_fixture", "character_id": "lin_che", "source_note": note,
            "image_sha256": digest_file(run / filename)}


def test_reanalysis_preserves_display_edit_and_registered_reference_and_snapshots_exact_bytes(poster_run, tmp_path, monkeypatch):
    asset = images.register_image(poster_run, poster_run / "test-only-1.png", fixture_receipt(poster_run))
    images.set_reference(poster_run, asset["asset_id"], "lin_che")
    reference = load_json(poster_run / "characters.json")["lin_che"]
    quotes = load_json(poster_run / "quotes.json")
    quotes[0].update(display_text="SIMULATED 用户显示编辑", text_mode="rewrite")
    save_json(poster_run / "quotes.json", quotes)
    path = tmp_path / "proposal.json"
    proposal_for(poster_run, path)
    original = path.read_bytes()
    actual = analysis.apply_analysis

    def replace_input_after_read(*args):
        path.write_bytes(b"changed after initial read")
        return actual(*args)

    monkeypatch.setattr(analysis, "apply_analysis", replace_input_after_read)
    result = analysis.apply_analysis_file(poster_run, path)
    assert result["proposal_hash"] == sha256(original)
    assert (poster_run / "analysis" / (sha256(original) + ".json")).read_bytes() == original
    updated = load_json(poster_run / "quotes.json")[0]
    assert updated["display_text"] == "SIMULATED 用户显示编辑" and updated["text_mode"] == "rewrite"
    assert updated["quote_exact"] == quotes[0]["quote_exact"]
    character = load_json(poster_run / "characters.json")["lin_che"]
    assert character["reference_asset_id"] == reference["reference_asset_id"]
    assert character["reference_image"] == reference["reference_image"]
    assert character["visual_anchor_version"] == reference["visual_anchor_version"]


def test_analysis_refuses_to_overwrite_a_concurrent_display_change(poster_run, tmp_path, monkeypatch):
    path = tmp_path / "proposal.json"
    proposal_for(poster_run, path)
    actual = analysis.commit_documents

    def race(run, values, **kwargs):
        quotes = load_json(run / "quotes.json")
        quotes[0].update(display_text="SIMULATED 并发改写", text_mode="rewrite")
        save_json(run / "quotes.json", quotes)
        return actual(run, values, **kwargs)

    monkeypatch.setattr(analysis, "commit_documents", race)
    with pytest.raises(ValueError, match="changed since edit snapshot"):
        analysis.apply_analysis_file(poster_run, path)
    assert load_json(poster_run / "quotes.json")[0]["display_text"] == "SIMULATED 并发改写"


@pytest.mark.parametrize("operation", ["attach", "reference", "registry"])
def test_image_document_writers_preserve_concurrent_changes(poster_run, monkeypatch, operation):
    asset = images.register_image(poster_run, poster_run / "test-only-1.png", fixture_receipt(poster_run))
    actual = images.commit_documents
    filename = {"attach": "cards.json", "reference": "characters.json", "registry": "image-assets.json"}[operation]
    before = load_json(poster_run / filename)
    concurrent = copy.deepcopy(before)
    if operation == "attach":
        concurrent[0]["style_id"] = "dark_stage"
    elif operation == "reference":
        concurrent["lin_che"]["traits"].append({"kind": "design_choice", "value": "SIMULATED CONCURRENT", "evidence": []})
    else:
        concurrent["SIMULATED_CONCURRENT_ENTRY"] = {"preserve": True}

    def race(run, values, **kwargs):
        save_json(run / filename, concurrent)
        return actual(run, values, **kwargs)

    monkeypatch.setattr(images, "commit_documents", race)
    with pytest.raises(ValueError, match="changed since edit snapshot"):
        if operation == "attach":
            images.attach_image(poster_run, asset["asset_id"], "card001")
        elif operation == "reference":
            images.set_reference(poster_run, asset["asset_id"], "lin_che")
        else:
            images.register_image(poster_run, poster_run / "test-only-2.png", fixture_receipt(poster_run, "test-only-2.png", "DIFFERENT SIMULATED RECEIPT"))
    assert load_json(poster_run / filename) == concurrent


def test_render_pointer_commit_does_not_undo_concurrent_style_and_retries_safely(poster_run, chinese_font, monkeypatch):
    actual = rendering.commit_documents

    def race(run, values, **kwargs):
        cards = load_json(run / "cards.json")
        cards[0]["style_id"] = "dark_stage"
        save_json(run / "cards.json", cards)
        return actual(run, values, **kwargs)

    monkeypatch.setattr(rendering, "commit_documents", race)
    with pytest.raises(ValueError, match="changed since edit snapshot"):
        rendering.render_cards(poster_run, chinese_font, card_id="card001")
    assert load_json(poster_run / "cards.json")[0]["style_id"] == "dark_stage"
    uncommitted = list((poster_run / "posters").glob("*.png"))
    hashes = {p: digest_file(p) for p in uncommitted}
    monkeypatch.setattr(rendering, "commit_documents", actual)
    result = rendering.render_cards(poster_run, chinese_font, card_id="card001")
    assert result["rendered"] == 1 and result["image_tool_calls"] == 0
    assert all(digest_file(p) == h for p, h in hashes.items())


def test_mid_composition_file_change_never_becomes_a_reusable_tracked_result(poster_run, chinese_font, monkeypatch):
    background = poster_run / "test-only-1.png"
    original = background.read_bytes()
    actual = rendering.layout.compose

    def race(*args, **kwargs):
        report = actual(*args, **kwargs)
        Image.new("RGB", (600, 900), "red").save(background)
        return report

    monkeypatch.setattr(rendering.layout, "compose", race)
    with pytest.raises(ValueError, match="changed during composition"):
        rendering.render_cards(poster_run, chinese_font, card_id="card001")
    report = next((poster_run / "posters").glob("*.layout.json"))
    assert "input_fingerprint" not in json.loads(report.read_text(encoding="utf-8"))
    background.write_bytes(original)
    monkeypatch.setattr(rendering.layout, "compose", actual)
    result = rendering.render_cards(poster_run, chinese_font, card_id="card001")
    assert result["rendered"] == 1 and "attempt2" in result["cards"][0]["poster"]


@pytest.mark.parametrize("operation", ["append", "registry"])
def test_real_parallel_connections_refuse_stale_writes_and_retry_without_lost_records(poster_run, monkeypatch, operation):
    with Ledger(poster_run / ".state/ledger.sqlite"):
        pass  # Published runs already have a migrated ledger before concurrent operations.
    if operation == "append":
        save_json(poster_run / "cards.json", load_json(poster_run / "cards.json")[:1])
        quotes = load_json(poster_run / "quotes.json")
        for quote in quotes:
            quote["selection"]["selected"] = True
        save_json(poster_run / "quotes.json", quotes)
    barrier = threading.Barrier(2)
    actual = images.commit_documents

    def synchronized(*args, **kwargs):
        barrier.wait(timeout=10)
        return actual(*args, **kwargs)

    monkeypatch.setattr(images, "commit_documents", synchronized)

    def attempt(index):
        try:
            if operation == "append":
                images.prepare_cards(poster_run, append=True)
            else:
                name = f"test-only-{index+1}.png"
                images.register_image(poster_run, poster_run / name,
                    fixture_receipt(poster_run, name, f"SIMULATED PARALLEL RECEIPT {index}"))
            return "committed"
        except ValueError as error:
            assert "changed since edit snapshot" in str(error)
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=20) for future in [pool.submit(attempt, 0), pool.submit(attempt, 1)]]
    assert sorted(results) == ["committed", "conflict"]
    monkeypatch.setattr(images, "commit_documents", actual)
    if operation == "append":
        assert [c["card_id"] for c in load_json(poster_run / "cards.json")] == ["card001", "card002"]
        assert images.prepare_cards(poster_run, append=True)["reused"]
    else:
        assert len(load_json(poster_run / "image-assets.json")) == 1
        for index in range(2):
            name = f"test-only-{index+1}.png"
            images.register_image(poster_run, poster_run / name,
                fixture_receipt(poster_run, name, f"SIMULATED PARALLEL RECEIPT {index}"))
        assert len(load_json(poster_run / "image-assets.json")) == 2
    assert images.budget_summary(poster_run)["reserved_calls"] == 0
