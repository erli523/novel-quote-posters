import copy
import json
import shutil

import pytest
from PIL import Image

from novel_quote_posters import style_review as review
from novel_quote_posters.contracts import default_definition
from novel_quote_posters.documents import commit_documents, recover_documents
from novel_quote_posters.images import budget_summary, register_image, set_reference
from novel_quote_posters.ingest import chunks, ingest
from novel_quote_posters.layout import STYLES, compose
from novel_quote_posters.quotes import candidates
from novel_quote_posters.storage import digest_file, fingerprint, load_json, save_json


@pytest.fixture(scope="module")
def sample_seed(tmp_path_factory, chinese_font):
    run = tmp_path_factory.mktemp("six-style-simulated-seed")
    source = run / "source.txt"
    source.write_text(
        "第一章 山路\n林澈说：“路还在。”\n他在雨里等灯。</script><script>malicious()</script>",
        encoding="utf-8",
    )
    book = ingest(source, title="雾山来信")
    quotes, _ = candidates(book)
    quote = quotes[0]
    quote["speaker"].update(
        status="explicit",
        character_id="lin_che",
        evidence=[
            {
                "start": book["text"].index("林澈说："),
                "end": book["text"].index("林澈说：") + 4,
                "text": "林澈说：",
            }
        ],
    )
    quote["quote_type"] = "dialogue"
    save_json(run / "book.json", book)
    save_json(run / "chunks.json", chunks(book, 6000, 300))
    save_json(run / "quotes.json", quotes)
    save_json(run / "cards.json", [])
    save_json(
        run / "characters.json",
        {"lin_che": {"character_id": "lin_che", "name": "林澈", "traits": []}},
    )
    definition = default_definition(
        book,
        "SIMULATED_SIX_STYLE_SOFTWARE_TEST",
        count=1,
        characters=["lin_che"],
        styles=list(STYLES),
    )
    definition.update(
        min_quote_chars=1,
        input_rights_note="Self-authored synthetic software fixture; no actual human approval",
    )
    save_json(
        run / "task-contract.json",
        {
            "schema_version": 1,
            "version": 1,
            "definition": definition,
            "fingerprint": fingerprint({"version": 1, "definition": definition}),
            "editor": "SIMULATED TEST SETUP",
            "reason": "Synthetic test only",
            "recorded_at": "2026-11-24T00:00:00Z",
        },
    )
    picture = run / "synthetic-test-only.png"
    Image.new("RGB", (600, 900), "gray").save(picture)
    asset = register_image(
        run,
        picture,
        {
            "source_kind": "user_provided",
            "character_id": "lin_che",
            "image_sha256": digest_file(picture),
            "source_note": "SIMULATED SOFTWARE TEST REPLACEMENT, not an actual human photograph or generation",
        },
    )
    set_reference(run, asset["asset_id"], "lin_che")
    folder = run / "samples"
    folder.mkdir()
    rows = []
    for style in STYLES:
        output = folder / (style + ".png")
        report = compose(
            run / asset["path"],
            output,
            text=quote["display_text"],
            character="林澈",
            title=book["title"],
            chapter=quote["source"]["chapter"],
            font_path=chinese_font,
            style_id=style,
            draft=True,
        )
        rows.append(
            {
                "style_id": style,
                "asset_id": asset["asset_id"],
                "poster_sha256": report["output_sha256"],
                "path": output.name,
                "background_sha256": asset["sha256"],
                "font_sha256": report["font_sha256"],
                "layout_overrides": None,
            }
        )
    save_json(
        folder / "manifest.json",
        {
            "schema_version": 1,
            "source_hash": book["source_hash"],
            "quote_id": quote["quote_id"],
            "quote_exact": quote["quote_exact"],
            "character_id": "lin_che",
            "reference_sha256": asset["sha256"],
            "styles": rows,
        },
    )
    return run


@pytest.fixture
def style_run(sample_seed, tmp_path):
    run = tmp_path / "run"
    shutil.copytree(sample_seed, run)
    return run


def prepare(run, tmp_path):
    path = tmp_path / "review.html"
    review.write_style_review_page(run, "samples", path)
    return load_json(path.with_suffix(".targets.json"))


def simulated(form):
    """Simulated signature in temporary test run only; never counts as human evidence."""
    record = copy.deepcopy(form)
    record.update(
        reviewer="SIMULATED SIX STYLE TEST REVIEWER",
        attested=True,
        reviewed_at="2026-11-24T00:00:00+00:00",
    )
    for row in record["styles"]:
        row.update(status="approved", checks=dict.fromkeys(review.CHECKS, "passed"))
    for row in record["pairs"]:
        row.update(human_status="approved", human_dimensions=list(review.DIMENSIONS)[:3])
    return record


def test_pending_page_binds_all_views_context_and_never_signs(style_run, tmp_path):
    before = {p.name: digest_file(p) for p in [style_run / "quotes.json", style_run / "cards.json"]}
    assert review.style_review_status(style_run)["state"] == "not_started"
    form = prepare(style_run, tmp_path)
    assert review.style_review_status(style_run)["state"] == "pending"
    assert not form["attested"] and form["reviewer"] == ""
    assert all(r["status"] == "pending" for r in form["styles"])
    assert len(form["pairs"]) == 15 and all(not r["human_dimensions"] for r in form["pairs"])
    assert all(len(r["view_hashes"]) == 4 for r in form["target"]["material"]["styles"])
    assert form["target"]["material"]["source_context"]["passages"]
    page = (tmp_path / "review.html").read_text("utf-8")
    assert "</script><script>malicious()" not in page and "\\u003c/script>" in page
    assert "fetch(" not in page and "XMLHttpRequest" not in page
    assert before == {name: digest_file(style_run / name) for name in before}
    with pytest.raises(ValueError, match="attestation"):
        review.apply_style_review(style_run, form)
    assert not (style_run / review.REVIEW_DOCUMENT).exists()


def test_simulated_roundtrip_is_idempotent_supplementary_only_and_cli_integrated(
    style_run, tmp_path, monkeypatch, capsys
):
    from novel_quote_posters.cli import main

    form = prepare(style_run, tmp_path)
    record = simulated(form)
    before = {
        name: digest_file(style_run / name)
        for name in ["quotes.json", "cards.json", "characters.json"]
    }
    result = review.apply_style_review(style_run, record)
    assert (
        result["state"] == "approved"
        and result["main_cards_changed"] == result["quote_approvals_changed"] == 0
    )
    assert review.style_review_status(style_run)["state"] == "approved"
    count = len(list((style_run / ".state/transactions").glob("*.json")))
    assert review.apply_style_review(style_run, record)["reused"]
    assert len(list((style_run / ".state/transactions").glob("*.json"))) == count
    assert before == {name: digest_file(style_run / name) for name in before}
    path = tmp_path / "SIMULATED-record.json"
    save_json(path, record)
    monkeypatch.setattr(
        "sys.argv", ["novel-posters", "apply-style-review", str(path), "--run", str(style_run)]
    )
    main()
    assert json.loads(capsys.readouterr().out)["reused"]
    assert budget_summary(style_run)["reserved_calls"] == 0


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(schema_version=1),
        lambda r: r.update(attested=False),
        lambda r: r.update(reviewer=""),
        lambda r: r.update(reviewed_at="2026-11-24T00:00:00"),
        lambda r: r.update(reviewer_type="AI"),
        lambda r: r["styles"].pop(),
        lambda r: r["styles"].__setitem__(0, r["styles"][1]),
        lambda r: r["styles"][0]["checks"].update(text="pending"),
        lambda r: r["styles"][0]["checks"].update(extra="passed"),
        lambda r: r["styles"][0].update(status=[]),
        lambda r: r["pairs"].pop(),
        lambda r: r["pairs"].__setitem__(0, r["pairs"][1]),
        lambda r: r["pairs"][0].update(human_dimensions=["composition"] * 3),
        lambda r: r["pairs"][0].update(human_dimensions=["composition", "typography"]),
        lambda r: r["pairs"][0].update(human_dimensions=["composition", "typography", "invalid"]),
        lambda r: r["target"]["material"]["source_context"]["passages"][0].update(
            text="tampered context"
        ),
        lambda r: r["registration"].update(version=999),
    ],
)
def test_invalid_record_never_partially_commits(style_run, tmp_path, mutation):
    record = simulated(prepare(style_run, tmp_path))
    mutation(record)
    with pytest.raises(ValueError):
        review.apply_style_review(style_run, record)
    assert not (style_run / review.REVIEW_DOCUMENT).exists()
    assert not list((style_run / ".state/style-review-records").glob("*.json"))


def test_rejection_and_partial_work_are_retained_without_approval(style_run, tmp_path):
    form = prepare(style_run, tmp_path)
    form.update(
        reviewer="SIMULATED TEST REVIEWER", attested=True, reviewed_at="2026-11-24T00:00:00Z"
    )
    form["styles"][0].update(status="rejected", note="SIMULATED: theme mismatch")
    assert review.apply_style_review(style_run, form)["state"] == "rejected"
    assert review.style_review_status(style_run)["state"] == "rejected"
    review.write_style_review_page(style_run, "samples", tmp_path / "review-next.html")
    form = load_json(tmp_path / "review-next.targets.json")
    form.update(
        reviewer="SIMULATED TEST REVIEWER", attested=True, reviewed_at="2026-11-24T00:00:00Z"
    )
    assert review.apply_style_review(style_run, form)["state"] == "pending"
    assert review.style_review_status(style_run)["state"] == "pending"


def test_corrupt_pixels_stale_manifest_and_registered_version_are_rejected(style_run, tmp_path):
    record = simulated(prepare(style_run, tmp_path))
    manifest_path = style_run / "samples/manifest.json"
    original = load_json(manifest_path)
    changed = copy.deepcopy(original)
    changed["reason"] = "changed selection intent"
    save_json(manifest_path, changed)
    assert review.style_review_status(style_run)["state"] == "invalidated"
    with pytest.raises(ValueError, match="content changed"):
        review.apply_style_review(style_run, record)
    review.write_style_review_page(style_run, "samples", tmp_path / "review-v2.html")
    save_json(manifest_path, original)
    review.write_style_review_page(style_run, "samples", tmp_path / "review-v3.html")
    with pytest.raises(ValueError, match="registration changed"):
        review.apply_style_review(style_run, record)
    assert review.style_review_status(style_run)["state"] == "pending"
    image = style_run / "samples/mist_cinema.png"
    image.write_bytes(b"corrupted PNG")
    assert review.style_review_status(style_run)["state"] == "invalidated"
    with pytest.raises(ValueError, match="differs"):
        review.style_review_targets(style_run, "samples")


def test_duplicate_styles_and_escaping_sample_paths_are_rejected(style_run):
    path = style_run / "samples/manifest.json"
    manifest = load_json(path)
    manifest["styles"][0] = manifest["styles"][1]
    save_json(path, manifest)
    with pytest.raises(ValueError, match="unique style"):
        review.style_review_targets(style_run, "samples")
    with pytest.raises(ValueError, match="escapes"):
        review.style_review_targets(style_run, "../outside")


def test_changed_speaker_and_scene_context_invalidate_record(style_run, tmp_path):
    record = simulated(prepare(style_run, tmp_path))
    review.apply_style_review(style_run, record)
    characters = load_json(style_run / "characters.json")
    characters["lin_che"]["traits"].append(
        {"kind": "design_choice", "value": "SIMULATED changed coat"}
    )
    commit_documents(
        style_run, {"characters.json": characters}, reason="SIMULATED character correction"
    )
    assert review.style_review_status(style_run)["state"] == "invalidated"
    with pytest.raises(ValueError, match="content changed"):
        review.apply_style_review(style_run, record)


def test_import_rechecks_inputs_and_concurrent_record_under_commit_lock(
    style_run, tmp_path, monkeypatch
):
    record = simulated(prepare(style_run, tmp_path))
    actual = review.commit_documents

    def concurrent(run, changed, **options):
        quotes = load_json(run / "quotes.json")
        quotes[0]["speaker"]["explanation"] = "SIMULATED concurrent semantic correction"
        actual(run, {"quotes.json": quotes}, reason="SIMULATED concurrent update")
        return actual(run, changed, **options)

    monkeypatch.setattr(review, "commit_documents", concurrent)
    with pytest.raises(ValueError, match="changed during import"):
        review.apply_style_review(style_run, record)
    assert not (style_run / review.REVIEW_DOCUMENT).exists()


def test_old_approval_cannot_replace_later_human_rejection_with_unchanged_pixels(
    style_run, tmp_path
):
    old = simulated(prepare(style_run, tmp_path))
    review.apply_style_review(style_run, old)
    review.write_style_review_page(style_run, "samples", tmp_path / "reconsider.html")
    rejection = load_json(tmp_path / "reconsider.targets.json")
    rejection.update(
        reviewer="SIMULATED SECOND HUMAN", attested=True, reviewed_at="2026-11-24T01:00:00Z"
    )
    rejection["styles"][0].update(status="rejected", note="SIMULATED later rejection")
    review.apply_style_review(style_run, rejection)
    with pytest.raises(ValueError, match="later human decisions"):
        review.apply_style_review(style_run, old)
    assert review.style_review_status(style_run)["state"] == "rejected"


def test_concurrent_record_update_is_not_overwritten(style_run, tmp_path, monkeypatch):
    record = simulated(prepare(style_run, tmp_path))
    actual = review.commit_documents
    competing = {
        "schema_version": 1,
        "status": "pending",
        "record": {"note": "SIMULATED concurrent decision"},
    }

    def concurrent(run, changed, **options):
        actual(run, {review.REVIEW_DOCUMENT: competing}, reason="SIMULATED concurrent decision")
        return actual(run, changed, **options)

    monkeypatch.setattr(review, "commit_documents", concurrent)
    with pytest.raises(ValueError, match="changed since edit snapshot"):
        review.apply_style_review(style_run, record)
    assert load_json(style_run / review.REVIEW_DOCUMENT) == competing


def test_interruption_after_commit_recovers_same_result_without_duplicate_history(
    style_run, tmp_path, monkeypatch
):
    record = simulated(prepare(style_run, tmp_path))
    actual = review.commit_documents

    def interrupted(*args, **kwargs):
        actual(*args, **kwargs)
        raise OSError("SIMULATED return lost after durable commit")

    monkeypatch.setattr(review, "commit_documents", interrupted)
    with pytest.raises(OSError, match="return lost"):
        review.apply_style_review(style_run, record)
    monkeypatch.setattr(review, "commit_documents", actual)
    recover_documents(style_run)
    assert review.apply_style_review(style_run, record)["reused"]
    assert len(list((style_run / ".state/style-review-records").glob("*.json"))) == 1
