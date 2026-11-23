
import pytest

from novel_quote_posters import images
from novel_quote_posters.export import export_bundle
from novel_quote_posters.rendering import render_cards
from novel_quote_posters.review import apply_review, review_targets
from novel_quote_posters.storage import digest_file, load_json, save_json
from novel_quote_posters.workflow import Ledger


def request(call_id="test-call", asset_id="test-portrait"):
    return {"call_id": call_id, "asset_id": asset_id, "kind": "reference",
            "character_id": "lin_che", "tool": "image_gen.imagegen", "references": [],
            "prompt": "SIMULATED TEST REQUEST. No image tool is called by these tests."}


def simulated_receipt(run, plan):
    # The simulator tests provenance plumbing; this is never a real tool-generation claim.
    return {"source_kind": "generated", "call_id": plan["call_id"],
            "input_hash": plan["input_hash"], "image_sha256": digest_file(run / "test-only-1.png"),
            "character_id": "lin_che", "tool_output_reference": "SIMULATED_TEST_ONLY_NO_REAL_CALL"}


def test_plan_is_bounded_and_unknown_results_require_reconciliation(poster_run):
    with Ledger(poster_run / ".state/ledger.sqlite", limit=2):
        pass
    original = request()
    assert images.plan_image(poster_run, original)["action"] == "invoke"
    assert images.plan_image(poster_run, original)["action"] == "reconcile"
    images.record_failure(poster_run, "test-call", "unknown_outcome", failure_kind="unknown", note="simulated timeout")
    retry = {**original, "call_id": "retry", "retry_of": "test-call"}
    with pytest.raises(ValueError, match="unknown outcome"):
        images.plan_image(poster_run, retry)
    duplicate = {**original, "call_id": "duplicate", "new_candidate_reason": "simulated variation"}
    with pytest.raises(ValueError, match="reconcile"):
        images.plan_image(poster_run, duplicate)
    images.record_failure(poster_run, "test-call", "failed", failure_kind="transient", note="confirmed simulator failure")
    assert images.plan_image(poster_run, retry)["action"] == "invoke"
    with pytest.raises(ValueError, match="budget exhausted"):
        images.plan_image(poster_run, request("third", "another-asset"))
    status = images.budget_summary(poster_run)
    assert status["reserved_calls"] == 2 and status["remaining"] == 0
    assert original["prompt"] not in str(status)


def test_retry_cannot_chain_or_rewrite_a_terminal_outcome(poster_run):
    original = request()
    images.plan_image(poster_run, original)
    images.record_failure(poster_run, "test-call", "failed", failure_kind="transient", note="simulated")
    retry = {**original, "call_id": "retry", "retry_of": "test-call"}
    images.plan_image(poster_run, retry)
    images.record_failure(poster_run, "retry", "failed", failure_kind="transient", note="simulated")
    with pytest.raises(ValueError, match="retry a retry"):
        images.plan_image(poster_run, {**original, "call_id": "retry2", "retry_of": "retry"})
    with pytest.raises(ValueError, match="terminal"):
        images.record_failure(poster_run, "test-call", "tool_unavailable", failure_kind="unavailable", note="different claim")


def test_generated_flag_alone_is_not_provenance_and_import_is_idempotent(poster_run):
    receipt = simulated_receipt(poster_run, {"call_id": "test-call", "input_hash": "not-planned"})
    with pytest.raises(ValueError, match="reserved"):
        images.register_image(poster_run, poster_run / "test-only-1.png", receipt)
    plan = images.plan_image(poster_run, request())
    receipt = simulated_receipt(poster_run, plan)
    asset = images.register_image(poster_run, poster_run / "test-only-1.png", receipt)
    assert images.register_image(poster_run, poster_run / "test-only-1.png", receipt) == asset
    assert images.budget_summary(poster_run)["reserved_calls"] == 1
    images.attach_image(poster_run, asset["asset_id"], "card001")
    card = load_json(poster_run / "cards.json")[0]
    assert images.validate_image_asset(poster_run, card, required=True) == asset
    changed = {**receipt, "tool_output_reference": "DIFFERENT_SIMULATED_RESULT"}
    with pytest.raises(ValueError, match="different registered output"):
        images.register_image(poster_run, poster_run / "test-only-1.png", changed)
    assert len(load_json(poster_run / "image-assets.json")) == 1


def test_result_registration_crash_replays_without_another_tool_call(poster_run, monkeypatch):
    plan = images.plan_image(poster_run, request())
    receipt = simulated_receipt(poster_run, plan)
    actual = Ledger.resolve_call

    def interrupted(self, *args, **kwargs):
        raise OSError("injected loss after asset registration before result reconciliation")

    monkeypatch.setattr(Ledger, "resolve_call", interrupted)
    with pytest.raises(OSError, match="injected"):
        images.register_image(poster_run, poster_run / "test-only-1.png", receipt)
    asset = next(iter(load_json(poster_run / "image-assets.json").values()))
    with pytest.raises(ValueError, match="reconciled"):
        images.attach_image(poster_run, asset["asset_id"], "card001")
    monkeypatch.setattr(Ledger, "resolve_call", actual)
    images.register_image(poster_run, poster_run / "test-only-1.png", receipt)
    assert images.budget_summary(poster_run)["reserved_calls"] == 1
    images.attach_image(poster_run, asset["asset_id"], "card001")


def test_receipt_tampering_is_detected_and_reference_setting_is_idempotent(poster_run):
    plan = images.plan_image(poster_run, request())
    asset = images.register_image(poster_run, poster_run / "test-only-1.png", simulated_receipt(poster_run, plan))
    images.set_reference(poster_run, asset["asset_id"], "lin_che")
    before = (poster_run / "characters.json").read_bytes()
    images.set_reference(poster_run, asset["asset_id"], "lin_che")
    assert (poster_run / "characters.json").read_bytes() == before
    images.attach_image(poster_run, asset["asset_id"], "card001")
    receipt = load_json(poster_run / asset["receipt"])
    receipt["tool_output_reference"] = "tampered"
    save_json(poster_run / asset["receipt"], receipt)
    with pytest.raises(ValueError, match="provenance changed"):
        images.validate_image_asset(poster_run, load_json(poster_run / "cards.json")[0])


def test_user_supplied_replacement_can_be_final_exported_after_simulated_review(poster_run, chinese_font, tmp_path):
    # Test-only human attestation and synthetic picture; not a real approved deliverable.
    receipt = {"source_kind": "user_provided", "character_id": "lin_che",
               "image_sha256": digest_file(poster_run / "test-only-1.png"),
               "source_note": "Synthetic replacement image in automated test, not an actual user photo."}
    asset = images.register_image(poster_run, poster_run / "test-only-1.png", receipt)
    for identity in ["card001", "card002"]:
        images.attach_image(poster_run, asset["asset_id"], identity)
    render_cards(poster_run, chinese_font, draft=False)
    form = review_targets(poster_run)
    form.update(reviewer="SIMULATED_TEST_REVIEWER", attested=True, reviewed_at="2026-11-22T00:00:00Z")
    for row in form["quotes"] + form["cards"]:
        row["status"] = "approved"
    for row in form["cards"]:
        row["visual_checks"] = dict.fromkeys(row["visual_checks"], "passed")
    apply_review(poster_run, form)
    report = export_bundle(poster_run, tmp_path / "final")
    assert all(a["image_kind"] == "user_provided" for a in report["assets"])
    assert images.budget_summary(poster_run)["reserved_calls"] == 0


def test_selected_cards_report_shortfall_without_inventing_quotes(poster_run):
    (poster_run / "cards.json").unlink()
    quotes = load_json(poster_run / "quotes.json")
    quotes[0]["selection"].update(selected=True, score=80)
    save_json(poster_run / "quotes.json", quotes)
    result = images.prepare_cards(poster_run, count=10)
    assert result["prepared"] == 1 and result["shortfall"] == 9
    with pytest.raises(ValueError, match="already exist"):
        images.prepare_cards(poster_run)


def test_legacy_call_counts_are_preserved_and_never_reset(poster_run, tmp_path):
    log = tmp_path / "legacy.json"
    save_json(log, {"calls": [{"call_id": "old", "state": "tool_unavailable", "error": "simulator"}]})
    images.import_legacy_failures(poster_run, log)
    images.import_legacy_failures(poster_run, log)
    status = images.budget_summary(poster_run)
    assert status["reserved_calls"] == 1 and status["remaining"] == 9


def test_generated_candidate_cannot_be_approved_using_a_test_baseline(poster_run, chinese_font):
    # All provider output and human decisions in this test are simulated.
    save_json(poster_run / "cards.json", load_json(poster_run / "cards.json")[:1])
    plan = images.plan_image(poster_run, {**request(), "kind": "background"})
    asset = images.register_image(poster_run, poster_run / "test-only-1.png", simulated_receipt(poster_run, plan))
    images.attach_image(poster_run, asset["asset_id"], "card001")
    baseline = images.register_image(poster_run, poster_run / "test-only-2.png", {
        "source_kind": "test_fixture", "character_id": "lin_che",
        "image_sha256": digest_file(poster_run / "test-only-2.png"), "source_note": "SIMULATED TEST BASELINE"})
    images.set_reference(poster_run, baseline["asset_id"], "lin_che")
    render_cards(poster_run, chinese_font, draft=False)
    form = review_targets(poster_run)
    form.update(reviewer="SIMULATED_TEST_REVIEWER", attested=True, reviewed_at="2026-11-23T00:00:00Z")
    for row in form["cards"]:
        row["status"] = "approved"
        row["visual_checks"] = dict.fromkeys(row["visual_checks"], "passed")
    with pytest.raises(ValueError, match="non-test baseline"):
        apply_review(poster_run, form)
    assert load_json(poster_run / "cards.json")[0]["review"]["status"] == "pending"
    assert images.budget_summary(poster_run)["reserved_calls"] == 1
