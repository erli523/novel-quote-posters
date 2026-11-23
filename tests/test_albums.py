import copy
import json
import zipfile

import pytest

from novel_quote_posters import review
from novel_quote_posters.albums import album_review_complete, album_status, load_album_review
from novel_quote_posters.bundles import verify_archive
from novel_quote_posters.export import export_bundle
from novel_quote_posters.images import attach_image, register_image
from novel_quote_posters.rendering import render_cards
from novel_quote_posters.storage import digest_file, load_json, save_json


def simulated_record(run, *, album=True):
    """SIMULATED test human, never actual source or visual approval."""
    form = review.review_targets(run)
    form.update(reviewer="SIMULATED_ALBUM_TEST_REVIEWER", attested=True, reviewed_at="2026-11-23T00:00:00Z")
    for row in form["quotes"] + form["cards"]:
        row["status"] = "approved"
    for row in form["cards"]:
        row["visual_checks"] = dict.fromkeys(row["visual_checks"], "passed")
    if album:
        form["album"]["status"] = "approved"
        form["album"]["checks"] = dict.fromkeys(form["album"]["checks"], "passed")
    return form


def prepare_replacement(run, font):
    # The picture is synthetic; this only tests the replacement-image path and gates.
    asset = register_image(run, run / "test-only-1.png", {
        "source_kind": "user_provided", "character_id": "lin_che",
        "image_sha256": digest_file(run / "test-only-1.png"), "source_note": "SIMULATED SOFTWARE TEST REPLACEMENT"})
    for card in load_json(run / "cards.json"):
        attach_image(run, asset["asset_id"], card["card_id"])
    render_cards(run, font, draft=False)


def test_single_card_approval_does_not_grant_album_export_and_order_changes_require_fresh_review(poster_run, chinese_font, tmp_path):
    prepare_replacement(poster_run, chinese_font)
    form = simulated_record(poster_run, album=False)
    review.apply_review(poster_run, form)
    with pytest.raises(ValueError, match="whole-album human approval"):
        export_bundle(poster_run, tmp_path / "unapproved")
    form = simulated_record(poster_run)
    assert review.apply_review(poster_run, form)["album_changed"]
    assert not review.apply_review(poster_run, form)["album_changed"]
    export_bundle(poster_run, tmp_path / "approved")
    cards = load_json(poster_run / "cards.json")
    before = {c["card_id"]: digest_file(poster_run / c["poster"]) for c in cards}
    save_json(poster_run / "cards.json", list(reversed(cards)))
    target = review.review_targets(poster_run)["album"]
    assert album_status(poster_run, target)["state"] == "invalidated"
    with pytest.raises(ValueError, match="whole-album human approval"):
        export_bundle(poster_run, tmp_path / "wrong-order")
    with pytest.raises(ValueError, match="whole-album review invalidated"):
        review.apply_review(poster_run, form)
    assert before == {c["card_id"]: digest_file(poster_run / c["poster"]) for c in cards}
    review.apply_review(poster_run, simulated_record(poster_run))
    result = export_bundle(poster_run, tmp_path / "new-order")
    assert result["assets"][0]["card_id"] == "card002"


def test_incomplete_album_checklist_applies_no_partial_single_card_decisions(poster_run, chinese_font):
    render_cards(poster_run, chinese_font, draft=False)
    form = simulated_record(poster_run)
    form["album"]["checks"]["reading_flow"] = "pending"
    before = (poster_run / "quotes.json").read_bytes()
    with pytest.raises(ValueError, match="whole-album checklist"):
        review.apply_review(poster_run, form)
    assert (poster_run / "quotes.json").read_bytes() == before
    assert not (poster_run / "album-review.json").exists()


def test_album_draft_approval_is_refused_even_with_individual_decisions_pending(poster_run, chinese_font):
    render_cards(poster_run, chinese_font)
    form = review.review_targets(poster_run)
    form.update(reviewer="SIMULATED_ALBUM_TEST_REVIEWER", attested=True, reviewed_at="2026-11-23T00:00:00Z")
    form["album"].update(status="approved", checks=dict.fromkeys(form["album"]["checks"], "passed"))
    with pytest.raises(ValueError, match="clean whole-album"):
        review.apply_review(poster_run, form)


def test_review_import_cannot_overwrite_a_concurrent_document_change(poster_run, monkeypatch):
    form = review.review_targets(poster_run, quotes_only=True)
    form.update(reviewer="SIMULATED_ALBUM_TEST_REVIEWER", attested=True, reviewed_at="2026-11-23T00:00:00Z")
    form["quotes"][0]["status"] = "approved"
    actual = review.commit_documents

    def race(run, values, **kwargs):
        characters = load_json(run / "characters.json")
        characters["lin_che"]["name"] = "a concurrent rename"
        save_json(run / "characters.json", characters)
        return actual(run, values, **kwargs)

    monkeypatch.setattr(review, "commit_documents", race)
    with pytest.raises(ValueError, match="changed since edit snapshot"):
        review.apply_review(poster_run, form)
    assert load_json(poster_run / "quotes.json")[0]["review"]["status"] == "pending"


def test_archive_verifier_checks_album_order_even_if_file_checksums_are_rebuilt(poster_run, chinese_font, tmp_path):
    render_cards(poster_run, chinese_font)
    output = tmp_path / "output"
    export_bundle(poster_run, output, draft=True)
    original = output / "draft-review.zip"
    assert verify_archive(original)["integrity"] == "passed"
    changed = tmp_path / "changed-order.zip"
    from novel_quote_posters.storage import sha256
    with zipfile.ZipFile(original) as archive:
        payload = {name: archive.read(name) for name in archive.namelist()}
    album = json.loads(payload["album-review.json"])
    album["material"]["ordered_cards"].reverse()
    payload["album-review.json"] = json.dumps(album).encode()
    manifest = json.loads(payload["manifest.json"])
    row = next(r for r in manifest["files"] if r["path"] == "album-review.json")
    row.update(size=len(payload["album-review.json"]), sha256=sha256(payload["album-review.json"]))
    payload["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(changed, "w") as archive:
        for name, raw in payload.items():
            archive.writestr(name, raw)
    with pytest.raises(ValueError, match="whole-album review/order mismatch"):
        verify_archive(changed)


def test_older_archive_schema_remains_integrity_verifiable_without_claiming_album_approval(poster_run, chinese_font, tmp_path):
    render_cards(poster_run, chinese_font)
    output = tmp_path / "output"
    export_bundle(poster_run, output, draft=True)
    older = tmp_path / "schema2.zip"
    with zipfile.ZipFile(output / "draft-review.zip") as archive:
        payload = {name: archive.read(name) for name in archive.namelist() if name != "album-review.json"}
    manifest = copy.deepcopy(json.loads(payload["manifest.json"]))
    manifest["schema_version"] = 2
    manifest["files"] = [r for r in manifest["files"] if r["path"] != "album-review.json"]
    payload["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(older, "w") as archive:
        for name, raw in payload.items():
            archive.writestr(name, raw)
    assert verify_archive(older)["authenticity"] == "not_authenticated"


def test_malformed_album_record_is_reported_as_invalid_instead_of_approved(poster_run):
    save_json(poster_run / "album-review.json", ["malformed persisted review"])
    with pytest.raises(ValueError, match="invalid whole-album review document"):
        load_album_review(poster_run)
    assert not album_review_complete([], {})
