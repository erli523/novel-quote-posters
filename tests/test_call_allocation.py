"""Local reservations only. No native tool/API calls or real human approvals."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from novel_quote_posters import contracts, images, review
from novel_quote_posters.bundles import verify_archive
from novel_quote_posters.export import export_bundle
from novel_quote_posters.rendering import render_cards
from novel_quote_posters.storage import fingerprint, load_json, save_json
from novel_quote_posters.workflow import Ledger


def form(run, **allocation):
    request = contracts.contract_template(run)
    request.update(editor="SIMULATED SOFTWARE TEST", reason="Explicit partition test; no actual image calls")
    request["definition"]["image_call_allocation"] = dict.fromkeys(contracts.ALLOCATION_BUCKETS, 0)
    request["definition"]["image_call_allocation"].update(allocation)
    request["definition"]["image_call_limit"] = sum(allocation.values())
    return request


def image_request(identity, **extra):
    return {"call_id": identity, "asset_id": identity, "kind": "reference", "tool": "image_gen.imagegen",
            "character_id": "lin_che", "references": [], "prompt": "SIMULATED LOCAL RESERVATION ONLY", **extra}


def legacy_contract(run):
    record = load_json(run / "task-contract.json")
    record["definition"].pop("image_call_allocation")
    record["definition"]["schema_version"] = 1
    record["fingerprint"] = fingerprint({"version": record["version"], "definition": record["definition"]})
    save_json(run / "task-contract.json", record)
    return record


def test_legacy_free_text_reason_is_not_reinterpreted_as_alternative_category():
    legacy = {"schema_version": 1, "request": image_request("SIMULATED_HISTORICAL", new_candidate_reason="capability recheck")}
    assert contracts.call_bucket(legacy) == "reference"
    assert contracts.call_bucket({**legacy, "schema_version": 2}) == "alternative"


@pytest.mark.parametrize("allocation", [
    {"reference": True}, {"reference": -1}, {"reference": 1.0}, {"unknown": 1}, {"reference": 2},
])
def test_invalid_partition_never_changes_contract(poster_run, allocation):
    request = form(poster_run, reference=1)
    request["definition"]["image_call_allocation"].update(allocation)
    before = (poster_run / "task-contract.json").read_bytes()
    with pytest.raises(ValueError, match="allocation"):
        contracts.apply_contract(poster_run, request)
    assert (poster_run / "task-contract.json").read_bytes() == before


def test_failure_consumes_partition_and_cannot_borrow_other_or_unallocated_capacity(poster_run):
    contracts.apply_contract(poster_run, form(poster_run, reference=1, background=3, retry=1, unallocated=2))
    original = image_request("SIMULATED_FIRST")
    images.plan_image(poster_run, original)
    images.record_failure(poster_run, original["call_id"], "tool_unavailable", failure_kind="unavailable", note="SIMULATED")
    with pytest.raises(ValueError, match="allocation exhausted: reference"):
        images.plan_image(poster_run, image_request("SIMULATED_SECOND"))
    summary = images.budget_summary(poster_run)
    assert (summary["remaining"], summary["usable_remaining"]) == (6, 4)
    assert summary["allocation"]["reference"] == {"limit": 1, "used": 1, "remaining": 0}
    assert summary["needs_reconciliation"] == 0 and summary["reserved_calls"] == 1
    assert images.plan_image(poster_run, image_request("SIMULATED_BG", kind="background"))["action"] == "invoke"
    with pytest.raises(ValueError, match="allocation exceeded: reference"):
        contracts.apply_contract(poster_run, form(poster_run, background=4, retry=1, unallocated=2))


def test_retry_and_alternative_use_separate_capacity_without_free_retries(poster_run):
    contracts.apply_contract(poster_run, form(poster_run, reference=2, retry=1, alternative=1, background=4))
    original = image_request("SIMULATED_ORIGINAL")
    images.plan_image(poster_run, original)
    images.record_failure(poster_run, original["call_id"], "failed", failure_kind="transient", note="SIMULATED")
    retry = {**original, "call_id": "SIMULATED_RETRY", "retry_of": original["call_id"]}
    images.plan_image(poster_run, retry)
    other = image_request("SIMULATED_OTHER")
    images.plan_image(poster_run, other)
    images.record_failure(poster_run, other["call_id"], "failed", failure_kind="transient", note="SIMULATED")
    with pytest.raises(ValueError, match="allocation exhausted: retry"):
        images.plan_image(poster_run, {**other, "call_id": "SIMULATED_RETRY2", "retry_of": other["call_id"]})
    images.plan_image(poster_run, {**other, "call_id": "SIMULATED_ALT", "new_candidate_reason": "SIMULATED design change"})
    with pytest.raises(ValueError, match="allocation exhausted: alternative"):
        images.plan_image(poster_run, image_request("SIMULATED_ALT2", purpose="alternative"))
    usage = images.budget_summary(poster_run)
    assert usage["allocation"]["retry"]["used"] == usage["allocation"]["alternative"]["used"] == 1
    assert usage["reserved_calls"] == 4


def test_legacy_upgrade_keeps_unknown_kind_and_all_call_facts(poster_run):
    old = legacy_contract(poster_run)
    book_bytes = (poster_run / "book.json").read_bytes()
    with Ledger(poster_run / ".state/ledger.sqlite") as ledger:
        ledger.reserve("looks-like-reference-but-unknown", "legacy-hash", request={"origin": "legacy_failure_log"})
        ledger.resolve_call("looks-like-reference-but-unknown", "tool_unavailable", details={"failure_kind": "unavailable"})
    with pytest.raises(ValueError, match="allocation missing"):
        images.plan_image(poster_run, image_request("SIMULATED_BLOCKED"))
    request = contracts.contract_template(poster_run)
    assert load_json(poster_run / "task-contract.json") == old
    allocation = request["definition"]["image_call_allocation"]
    assert allocation["unclassified"] == 1 and allocation["reference"] == 0 and allocation["unallocated"] == 9
    request.update(editor="SIMULATED MIGRATION", reason="Preserve facts, explicitly allocate remaining capacity")
    allocation.update(reference=1, unallocated=8)
    contracts.apply_contract(poster_run, request)
    summary = images.budget_summary(poster_run)
    assert summary["reserved_calls"] == 1 and summary["remaining"] == 9 and summary["usable_remaining"] == 1
    assert summary["calls"][0]["allocation_bucket"] == "unclassified"
    assert summary["calls"][0]["status"] == "tool_unavailable" and summary["needs_reconciliation"] == 0
    assert (poster_run / "book.json").read_bytes() == book_bytes
    assert summary["allocation_issues"] == []


def test_policy_revision_reconciles_same_operation_without_new_reservation(poster_run):
    original = image_request("SIMULATED_RECONCILE")
    first = images.plan_image(poster_run, original)
    contracts.apply_contract(poster_run, form(poster_run, reference=1, background=9))
    second = images.plan_image(poster_run, original)
    assert second["action"] == "reconcile" and second["input_hash"] == first["input_hash"]
    assert images.budget_summary(poster_run)["reserved_calls"] == 1
    with pytest.raises(ValueError, match="changed inputs"):
        images.plan_image(poster_run, {**original, "prompt": "SIMULATED CHANGED"})


def test_parallel_reservations_enforce_one_remaining_partition(poster_run, monkeypatch):
    contracts.apply_contract(poster_run, form(poster_run, reference=1, background=9))
    barrier = threading.Barrier(2)
    actual = images.validate_source

    def synchronized(*args):
        actual(*args)
        barrier.wait(timeout=10)

    monkeypatch.setattr(images, "validate_source", synchronized)

    def reserve(index):
        try:
            return images.plan_image(poster_run, image_request(f"SIMULATED_PARALLEL_{index}"))["action"]
        except ValueError as error:
            assert "allocation exhausted: reference" in str(error)
            return "partition_exhausted"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, range(2)))
    assert sorted(results) == ["invoke", "partition_exhausted"]
    assert images.budget_summary(poster_run)["reserved_calls"] == 1


def test_budget_race_rechecked_under_contract_commit_lock(poster_run, monkeypatch):
    request = form(poster_run, reference=1, background=9)
    actual = contracts.commit_documents
    before = (poster_run / "task-contract.json").read_bytes()

    def race(run, values, **kwargs):
        with Ledger(run / ".state/ledger.sqlite") as ledger:
            ledger.reserve("SIMULATED_CONCURRENT_UNKNOWN", "unknown")
        return actual(run, values, **kwargs)

    monkeypatch.setattr(contracts, "commit_documents", race)
    with pytest.raises(ValueError, match="budget changed.*unclassified"):
        contracts.apply_contract(poster_run, request)
    assert (poster_run / "task-contract.json").read_bytes() == before


def test_legacy_schema4_archive_preserves_selection_only_verification(poster_run, chinese_font, tmp_path):
    legacy_contract(poster_run)
    render_cards(poster_run, chinese_font)
    output = tmp_path / "legacy-definition-schema4"
    export_bundle(poster_run, output, draft=True)
    result = verify_archive(output / "draft-review.zip")
    assert result["integrity"] == "passed" and result["task_conformance"] == "passed"
    assert result["task_conformance_scope"] == "declared_selection_constraints_only"
    assert result["authenticity"] == "not_authenticated"
    targets = review.review_targets(poster_run)
    assert any("allocation missing" in issue for issue in targets["task_issues"])


def test_approval_commit_rechecks_budget_race_without_partial_decisions(poster_run, chinese_font, monkeypatch):
    render_cards(poster_run, chinese_font, draft=False)
    record = review.review_targets(poster_run)
    record.update(reviewer="SIMULATED TEST HUMAN", attested=True, reviewed_at="2026-11-23T00:00:00Z")
    record["album"].update(status="approved", checks=dict.fromkeys(record["album"]["checks"], "passed"))
    actual = review.commit_documents

    def race(run, values, **kwargs):
        with Ledger(run / ".state/ledger.sqlite") as ledger:
            ledger.reserve("SIMULATED_UNCLASSIFIED_RACE", "hash")
        return actual(run, values, **kwargs)

    monkeypatch.setattr(review, "commit_documents", race)
    with pytest.raises(ValueError, match="blocks approval.*unclassified"):
        review.apply_review(poster_run, record)
    assert not (poster_run / "album-review.json").exists()
