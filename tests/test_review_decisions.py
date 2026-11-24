"""Synthetic decisions only; no signature here is actual human product evidence."""

import copy

import pytest

from novel_quote_posters import review
from novel_quote_posters.documents import commit_documents, recover_documents
from novel_quote_posters.edits import apply_edits, edit_targets
from novel_quote_posters.rendering import render_cards
from novel_quote_posters.review_decisions import DOCUMENT, load_decisions
from novel_quote_posters.storage import load_json, save_json


def signed(run, *, status="approved", quotes_only=False):
    form = review.review_targets(run, quotes_only=quotes_only)
    form.update(
        reviewer="SIMULATED REVIEW DECISION TEST", attested=True, reviewed_at="2026-11-24T00:00:00Z"
    )
    for row in form["quotes"] + form["cards"]:
        row["status"] = status
    for row in form["cards"]:
        row["visual_checks"] = dict.fromkeys(
            row["visual_checks"], "passed" if status == "approved" else "failed"
        )
    if form["album"]:
        form["album"].update(
            status=status,
            checks=dict.fromkeys(
                form["album"]["checks"], "passed" if status == "approved" else "failed"
            ),
        )
    return form


def test_later_rejection_of_all_three_categories_cannot_be_replaced_by_old_approval(
    poster_run, chinese_font
):
    render_cards(poster_run, chinese_font, draft=False)
    old = signed(poster_run)
    review.apply_review(poster_run, old)
    later = signed(poster_run, status="rejected")
    review.apply_review(poster_run, later)
    files = ["quotes.json", "cards.json", "album-review.json", DOCUMENT]
    before = {name: (poster_run / name).read_bytes() for name in files}
    with pytest.raises(ValueError, match="later human decision"):
        review.apply_review(poster_run, old)
    assert before == {name: (poster_run / name).read_bytes() for name in files}
    _, versions = load_decisions(poster_run)
    assert set(versions["quotes"].values()) == set(versions["cards"].values()) == {2}
    assert versions["album"] == 2


@pytest.mark.parametrize("category", ["quotes", "cards", "album"])
def test_each_review_category_independently_rejects_stale_replay(
    poster_run, chinese_font, category
):
    render_cards(poster_run, chinese_font, draft=False)

    def scoped(status):
        record = signed(poster_run, status=status, quotes_only=category == "quotes")
        if category == "cards":
            record.update(quotes=[], album=None)
        if category == "album":
            record["quotes"] = []
            for row in record["cards"]:
                row["status"] = "pending"
        return record

    old = scoped("approved")
    review.apply_review(poster_run, old)
    review.apply_review(poster_run, scoped("rejected"))
    with pytest.raises(ValueError, match="later human decision"):
        review.apply_review(poster_run, old)


def test_identical_current_result_replay_does_not_increment_versions_or_transactions(poster_run):
    record = signed(poster_run, quotes_only=True)
    review.apply_review(poster_run, record)
    before = (poster_run / DOCUMENT).read_bytes()
    count = len(list((poster_run / ".state/transactions").glob("*.json")))
    result = review.apply_review(poster_run, record)
    assert result["quotes_changed"] == 0 and result["transaction"] is None
    assert (poster_run / DOCUMENT).read_bytes() == before
    assert len(list((poster_run / ".state/transactions").glob("*.json"))) == count


def test_old_partial_form_can_still_review_an_unchanged_different_target(poster_run):
    template = signed(poster_run, quotes_only=True)
    first = copy.deepcopy(template)
    first["quotes"][1]["status"] = "pending"
    review.apply_review(poster_run, first)
    second = copy.deepcopy(template)
    second["quotes"][0]["status"] = "pending"
    assert review.apply_review(poster_run, second)["quotes_changed"] == 1
    assert set(load_decisions(poster_run)[1]["quotes"].values()) == {1}


def test_one_stale_target_cannot_partially_commit_a_fresh_target(poster_run):
    old = signed(poster_run, quotes_only=True)
    one = copy.deepcopy(old)
    one["quotes"][1]["status"] = "pending"
    review.apply_review(poster_run, one)
    fresh = signed(poster_run, quotes_only=True)
    stale = copy.deepcopy(old["quotes"][0])
    stale["status"] = "rejected"
    fresh["quotes"] = [fresh["quotes"][1], stale]
    before = {name: (poster_run / name).read_bytes() for name in ["quotes.json", DOCUMENT]}
    with pytest.raises(ValueError, match="later human decision"):
        review.apply_review(poster_run, fresh)
    assert before == {name: (poster_run / name).read_bytes() for name in before}


@pytest.mark.parametrize("field", ["decision_revision", "prior_review_fingerprint"])
def test_missing_decision_snapshot_never_applies_approval(poster_run, field):
    record = signed(poster_run, quotes_only=True)
    record["quotes"][0].pop(field)
    with pytest.raises(ValueError, match="stale review snapshot"):
        review.apply_review(poster_run, record)
    assert not (poster_run / DOCUMENT).exists()


def test_old_schema_three_and_boolean_decision_version_are_rejected(poster_run):
    record = signed(poster_run, quotes_only=True)
    record["schema_version"] = 3
    with pytest.raises(ValueError, match="schema 4"):
        review.apply_review(poster_run, record)
    record["schema_version"] = 4
    record["quotes"][0]["decision_revision"] = False
    with pytest.raises(ValueError, match="stale review snapshot"):
        review.apply_review(poster_run, record)
    assert not (poster_run / DOCUMENT).exists()


def test_even_noop_import_requires_current_decision_snapshot_fields(poster_run):
    record = signed(poster_run, quotes_only=True)
    review.apply_review(poster_run, record)
    before = (poster_run / DOCUMENT).read_bytes()
    record["quotes"][0].pop("decision_revision")
    record["quotes"][0].pop("prior_review_fingerprint")
    with pytest.raises(ValueError, match="stale review snapshot"):
        review.apply_review(poster_run, record)
    assert (poster_run / DOCUMENT).read_bytes() == before


def test_decision_version_survives_source_roundtrip_and_exact_pending_snapshot_restoration(
    poster_run,
):
    original = copy.deepcopy(load_json(poster_run / "quotes.json")[0])
    old = signed(poster_run, quotes_only=True)
    old["quotes"] = old["quotes"][:1]
    review.apply_review(poster_run, old)
    for text, mode in [("还要走。", "rewrite"), (original["quote_exact"], "original")]:
        plan = edit_targets(poster_run)
        plan.update(
            editor="SIMULATED SOURCE ROUNDTRIP",
            reason="Test decision version retention",
            operations=[
                {
                    "type": "set-display-text",
                    "quote_id": original["quote_id"],
                    "display_text": text,
                    "text_mode": mode,
                }
            ],
        )
        apply_edits(poster_run, plan, allow_rewrite=True)
    quotes = load_json(poster_run / "quotes.json")
    quotes[0]["review"] = original.get("review")
    commit_documents(
        poster_run,
        {"quotes.json": quotes},
        reason="SIMULATED exact pending metadata restored; preserve decision history",
    )
    target = review.review_targets(poster_run, quotes_only=True)["quotes"][0]
    assert target["fingerprint"] == old["quotes"][0]["fingerprint"]
    assert target["prior_review_fingerprint"] == old["quotes"][0]["prior_review_fingerprint"]
    assert target["decision_revision"] == 1 and old["quotes"][0]["decision_revision"] == 0
    with pytest.raises(ValueError, match="later human decision"):
        review.apply_review(poster_run, old)


@pytest.mark.parametrize("damage", ["delete", "rollback"])
def test_missing_or_rolled_back_version_file_does_not_silently_restart_at_zero(poster_run, damage):
    review.apply_review(poster_run, signed(poster_run, quotes_only=True))
    first = (poster_run / DOCUMENT).read_bytes()
    review.apply_review(poster_run, signed(poster_run, status="rejected", quotes_only=True))
    if damage == "delete":
        (poster_run / DOCUMENT).unlink()
    else:
        (poster_run / DOCUMENT).write_bytes(first)
    with pytest.raises(ValueError, match="history missing or changed"):
        review.review_targets(poster_run, quotes_only=True)


@pytest.mark.parametrize(
    "value",
    [
        {"schema_version": 1, "quotes": {}, "cards": {}, "album": True},
        {"schema_version": 1, "quotes": {"q": -1}, "cards": {}, "album": 0},
        {"schema_version": 1, "quotes": {}, "cards": [], "album": 0},
    ],
)
def test_malformed_uncommitted_version_document_is_rejected(poster_run, value):
    save_json(poster_run / DOCUMENT, value)
    with pytest.raises(ValueError, match="invalid persistent"):
        review.review_targets(poster_run, quotes_only=True)


def test_interruption_after_atomic_commit_recovers_without_repeating_decision(
    poster_run, monkeypatch
):
    record = signed(poster_run, quotes_only=True)
    actual = review.commit_documents

    def lost(*args, **kwargs):
        actual(*args, **kwargs)
        raise OSError("SIMULATED return lost after review+version commit")

    monkeypatch.setattr(review, "commit_documents", lost)
    with pytest.raises(OSError, match="return lost"):
        review.apply_review(poster_run, record)
    monkeypatch.setattr(review, "commit_documents", actual)
    recover_documents(poster_run)
    before = (poster_run / DOCUMENT).read_bytes()
    assert review.apply_review(poster_run, record)["quotes_changed"] == 0
    assert (poster_run / DOCUMENT).read_bytes() == before


def test_concurrent_later_decision_is_preserved_at_commit_lock(poster_run, monkeypatch):
    old = signed(poster_run, quotes_only=True)
    competing = signed(poster_run, status="rejected", quotes_only=True)
    actual = review.commit_documents

    def concurrent(*args, **kwargs):
        with monkeypatch.context() as context:
            context.setattr(review, "commit_documents", actual)
            review.apply_review(poster_run, competing)
        return actual(*args, **kwargs)

    monkeypatch.setattr(review, "commit_documents", concurrent)
    with pytest.raises(ValueError, match="changed since edit snapshot"):
        review.apply_review(poster_run, old)
    assert all(q["review"]["status"] == "rejected" for q in load_json(poster_run / "quotes.json"))
    assert set(load_decisions(poster_run)[1]["quotes"].values()) == {1}
