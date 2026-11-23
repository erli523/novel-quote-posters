import json
import zipfile

import pytest

from novel_quote_posters import contracts
from novel_quote_posters.bundles import verify_archive
from novel_quote_posters.export import export_bundle
from novel_quote_posters.images import budget_summary, plan_image, prepare_cards
from novel_quote_posters.rendering import render_cards
from novel_quote_posters.review import apply_review, review_targets
from novel_quote_posters.status import run_status
from novel_quote_posters.storage import fingerprint, load_json, save_json, sha256
from novel_quote_posters.workflow import Ledger


def request(run):
    form = contracts.contract_template(run)
    form.update(editor="SIMULATED CONTRACT TEST", reason="Explicit self-authored test intent; no human content approval")
    return form


def test_legacy_template_does_not_infer_target_from_existing_cards_and_preserves_spent_calls(poster_run):
    (poster_run / "task-contract.json").unlink()  # Disposable temporary fixture, never real data.
    with Ledger(poster_run / ".state/ledger.sqlite") as ledger:
        ledger.reserve("SIMULATED_OLD_CALL", "hash")
    form = request(poster_run)
    assert form["definition"]["target_count"] == 10  # Existing fixture has two cards.
    assert form["definition"]["character_ids"] == []
    form["definition"].update(character_ids=["lin_che"], target_count=5, image_call_limit=3,
                               input_rights_note="Self-authored fictional test source")
    form["definition"]["image_call_allocation"] = dict.fromkeys(contracts.ALLOCATION_BUCKETS, 0)
    form["definition"]["image_call_allocation"].update(unclassified=1, reference=1, background=1)
    result = contracts.apply_contract(poster_run, form)
    assert result["version"] == 1 and contracts.apply_contract(poster_run, form)["reused"]
    status = run_status(poster_run)
    assert status["task_contract"]["requested"] == 5 and status["task_contract"]["shortfall"] == 3
    assert status["budget"]["reserved_calls"] == 1 and status["budget"]["remaining"] == 2
    assert status["budget"]["ledger_limit"] == 10


def test_contract_budget_cannot_expand_erase_calls_or_race_with_new_reservations(poster_run, monkeypatch):
    form = request(poster_run)
    form["definition"]["image_call_limit"] = 11
    form["definition"]["image_call_allocation"]["unallocated"] = 1
    with pytest.raises(ValueError, match="expand the existing budget"):
        contracts.apply_contract(poster_run, form)
    form["definition"]["image_call_limit"] = 1
    form["definition"]["image_call_allocation"] = dict.fromkeys(contracts.ALLOCATION_BUCKETS, 0)
    form["definition"]["image_call_allocation"]["reference"] = 1
    actual = contracts.commit_documents
    before = load_json(poster_run / "task-contract.json")

    def race(run, values, **kwargs):
        with Ledger(run / ".state/ledger.sqlite") as ledger:
            ledger.reserve("SIMULATED_RACE_A", "a")
            ledger.reserve("SIMULATED_RACE_B", "b")
        return actual(run, values, **kwargs)

    monkeypatch.setattr(contracts, "commit_documents", race)
    with pytest.raises(ValueError, match="budget changed"):
        contracts.apply_contract(poster_run, form)
    assert load_json(poster_run / "task-contract.json") == before
    monkeypatch.setattr(contracts, "commit_documents", actual)
    with pytest.raises(ValueError, match="erase spent calls"):
        contracts.apply_contract(poster_run, form)


def test_new_contract_invalidates_visual_records_without_regenerating_or_rerendering(poster_run, chinese_font):
    render_cards(poster_run, chinese_font, draft=False)
    old = review_targets(poster_run)
    old.update(reviewer="SIMULATED TEST HUMAN", attested=True, reviewed_at="2026-11-23T00:00:00Z")
    for row in old["cards"]:
        row["status"] = "approved"
        row["visual_checks"] = dict.fromkeys(row["visual_checks"], "passed")
    apply_review(poster_run, old)
    form = request(poster_run)
    form["definition"]["theme"] = "a new explicit scene requirement"
    result = contracts.apply_contract(poster_run, form)
    assert result["version"] == 2
    with pytest.raises(ValueError, match="invalidated"):
        apply_review(poster_run, old)
    assert render_cards(poster_run, chinese_font, draft=False)["reused"] == 2
    assert budget_summary(poster_run)["reserved_calls"] == 0


def test_contract_filters_candidates_and_records_quantity_and_buffer_shortfalls(poster_run):
    (poster_run / "cards.json").unlink()
    quotes = load_json(poster_run / "quotes.json")
    for quote in quotes:
        quote["selection"]["selected"] = True
    save_json(poster_run / "quotes.json", quotes)
    form = request(poster_run)
    form["definition"]["max_quote_chars"] = 4
    contracts.apply_contract(poster_run, form)
    with pytest.raises(ValueError, match="differs from task contract"):
        prepare_cards(poster_run, count=10)
    report = prepare_cards(poster_run)
    assert (report["requested"], report["prepared"], report["shortfall"]) == (2, 1, 1)
    assert report["candidate_buffer_target"] == 3 and report["candidate_buffer_shortfall"] == 2
    assert load_json(poster_run / "preparation-report.json") == {
        k: v for k, v in report.items() if k not in {"added", "reused", "transaction"}}


def test_card_preparation_does_not_overwrite_a_concurrently_created_album(poster_run, monkeypatch):
    from novel_quote_posters import images
    (poster_run / "cards.json").unlink()
    quotes = load_json(poster_run / "quotes.json")
    for quote in quotes:
        quote["selection"]["selected"] = True
    save_json(poster_run / "quotes.json", quotes)
    actual = images.commit_documents
    concurrent = [{"card_id": "SIMULATED_CONCURRENT_CARD_PRESERVE"}]

    def race(run, values, **kwargs):
        save_json(run / "cards.json", concurrent)
        return actual(run, values, **kwargs)

    monkeypatch.setattr(images, "commit_documents", race)
    with pytest.raises(ValueError, match="changed since edit snapshot"):
        prepare_cards(poster_run)
    assert load_json(poster_run / "cards.json") == concurrent
    assert not (poster_run / "preparation-report.json").exists()


def test_rewrite_flag_does_not_override_task_policy_and_zero_budget_stops_new_calls(poster_run):
    form = request(poster_run)
    form["definition"].update(allow_rewrite=False, image_call_limit=0)
    form["definition"]["image_call_allocation"] = dict.fromkeys(contracts.ALLOCATION_BUCKETS, 0)
    contracts.apply_contract(poster_run, form)
    from novel_quote_posters.edits import apply_edits, edit_targets
    edit = edit_targets(poster_run)
    edit.update(editor="SIMULATED TEST", reason="negative policy test", operations=[
        {"type": "set-display-text", "quote_id": load_json(poster_run / "quotes.json")[0]["quote_id"],
         "display_text": "改写文字。", "text_mode": "rewrite"}])
    with pytest.raises(ValueError, match="forbidden by task contract"):
        apply_edits(poster_run, edit, allow_rewrite=True)
    with pytest.raises(ValueError, match="task contract image budget exhausted"):
        plan_image(poster_run, {"call_id": "SIMULATED_NEVER_INVOKED", "asset_id": "fixture", "kind": "reference",
            "character_id": "lin_che", "tool": "image_gen.imagegen", "prompt": "SIMULATED REQUEST ONLY", "references": []})
    assert budget_summary(poster_run)["reserved_calls"] == 0


def test_missing_contract_cannot_grant_visual_or_album_approval(poster_run, chinese_font):
    render_cards(poster_run, chinese_font, draft=False)
    (poster_run / "task-contract.json").unlink()
    record = review_targets(poster_run)
    record.update(reviewer="SIMULATED TEST HUMAN", attested=True, reviewed_at="2026-11-23T00:00:00Z")
    record["album"].update(status="approved", checks=dict.fromkeys(record["album"]["checks"], "passed"))
    with pytest.raises(ValueError, match="task contract blocks album approval"):
        apply_review(poster_run, record)
    with pytest.raises(ValueError, match="explicit task contract required"):
        plan_image(poster_run, {"call_id": "SIMULATED_NO_CONTRACT", "asset_id": "fixture", "kind": "reference",
            "character_id": "lin_che", "tool": "image_gen.imagegen", "prompt": "SIMULATED REQUEST ONLY", "references": []})
    assert budget_summary(poster_run)["reserved_calls"] == 0


def test_changed_quantity_blocks_fresh_approval_without_touching_existing_images(poster_run, chinese_font):
    render_cards(poster_run, chinese_font, draft=False)
    before = {c["card_id"]: (poster_run / c["poster"]).read_bytes() for c in load_json(poster_run / "cards.json")}
    form = request(poster_run)
    form["definition"]["target_count"] = 3
    contracts.apply_contract(poster_run, form)
    record = review_targets(poster_run)
    record.update(reviewer="SIMULATED TEST HUMAN", attested=True, reviewed_at="2026-11-23T00:00:00Z")
    record["album"].update(status="approved", checks=dict.fromkeys(record["album"]["checks"], "passed"))
    with pytest.raises(ValueError, match="poster count 2 differs from requested 3"):
        apply_review(poster_run, record)
    assert run_status(poster_run)["task_contract"]["shortfall"] == 1
    assert before == {c["card_id"]: (poster_run / c["poster"]).read_bytes() for c in load_json(poster_run / "cards.json")}


@pytest.mark.parametrize("change", [
    {"allow_source_upload": True}, {"allowed_destinations": ["local", "unapproved_api"]},
    {"character_ids": ["unknown"]}, {"chapter_ids": ["unknown"]}, {"target_count": True},
    {"output": {"width": 1080, "height": 1920}},
])
def test_invalid_or_authority_expanding_contracts_are_rejected(poster_run, change):
    form = request(poster_run)
    form["definition"].update(change)
    before = (poster_run / "task-contract.json").read_bytes()
    with pytest.raises(ValueError):
        contracts.apply_contract(poster_run, form)
    assert (poster_run / "task-contract.json").read_bytes() == before


def test_previous_schema3_archive_stays_verifiable_without_contract_authentication(poster_run, chinese_font, tmp_path):
    render_cards(poster_run, chinese_font)
    output = tmp_path / "current"
    export_bundle(poster_run, output, draft=True)
    with zipfile.ZipFile(output / "draft-review.zip") as archive:
        payload = {name: archive.read(name) for name in archive.namelist() if name != "task-contract.json"}
    manifest = json.loads(payload["manifest.json"])
    manifest["schema_version"] = 3
    for field in ["task_contract_fingerprint", "contract_issues"]:
        manifest.pop(field)
    manifest["files"] = [row for row in manifest["files"] if row["path"] != "task-contract.json"]
    album = json.loads(payload["album-review.json"])
    album["material"].pop("task_contract_fingerprint")
    album["fingerprint"] = fingerprint(album["material"])
    payload["album-review.json"] = json.dumps(album).encode()
    row = next(row for row in manifest["files"] if row["path"] == "album-review.json")
    row.update(sha256=sha256(payload["album-review.json"]), size=len(payload["album-review.json"]))
    payload["manifest.json"] = json.dumps(manifest).encode()
    legacy = tmp_path / "legacy-schema3.zip"
    with zipfile.ZipFile(legacy, "w") as archive:
        for name, raw in payload.items():
            archive.writestr(name, raw)
    result = verify_archive(legacy)
    assert result["integrity"] == "passed" and result["task_conformance"] == "not_checked_legacy"
    assert result["authenticity"] == "not_authenticated"
