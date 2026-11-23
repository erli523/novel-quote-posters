"""Self-authored attribution cases; author expectations are not human gold labels."""

import copy
import json
from pathlib import Path

import pytest

from novel_quote_posters.analysis import apply_analysis, apply_analysis_file, evidence
from novel_quote_posters.bootstrap import initialize_run
from novel_quote_posters.contracts import apply_contract, contract_template
from novel_quote_posters.images import plan_image
from novel_quote_posters.ingest import chunks, ingest
from novel_quote_posters.quotes import locate, make_quote, verify
from novel_quote_posters.review import apply_review, review_targets, write_review_page
from novel_quote_posters.source_context import CONTEXT_POLICY_VERSION, SourceContext
from novel_quote_posters.storage import load_json, save_json
from novel_quote_posters.workflow import Ledger

ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "fixtures/temporal_dialogue.txt"
PROPOSAL = ROOT / "fixtures/temporal_dialogue.application_analysis.json"


@pytest.fixture
def temporal_run(tmp_path):
    run = tmp_path / "temporal-run"
    initialize_run(SOURCE, run, size=160, overlap=48, image_limit=0)
    apply_analysis_file(run, PROPOSAL)
    return run


def test_temporal_proposals_match_authored_cases_but_remain_unapproved():
    book = ingest(SOURCE)
    windows = chunks(book, 160, 48)
    expected = load_json(ROOT / "fixtures/temporal_dialogue.expected.json")
    quotes, _, updated = apply_analysis(book, windows, load_json(PROPOSAL))
    assert len(quotes) == len(expected["cases"]) == 11 and len(updated) == 7
    assert all(row["semantic_status"] == "application_processed" for row in updated)
    for case in expected["cases"]:
        quote = next(q for q in quotes if q["source"]["start"] == case["start"])
        assert verify(book, quote)
        assert (quote["quote_exact"], quote["speaker"]["character_id"], quote["speaker"]["status"], quote["quote_type"]) == (
            case["quote_exact"], case["character_id"], case["speaker_status"], case["quote_type"])
        if case["in_quotation_marks"]:
            assert book["text"][quote["source"]["start"]-1] == "“"
            assert book["text"][quote["source"]["end"]] == "”"
        assert quote["review"]["status"] == "pending"
    duplicates = [q for q in quotes if q["quote_exact"] == "我会等到天亮。"]
    assert len({q["quote_id"] for q in duplicates}) == 2
    assert [q["speaker"]["character_id"] for q in duplicates] == ["zhou_che", "lin_che"]
    with pytest.raises(ValueError, match="2 exact occurrences"):
        locate(book, "我会等到天亮。")
    multiline = next(q for q in quotes if "\n" in q["quote_exact"])
    assert len(multiline["source"]["paragraph_ids"]) == 2


def test_distant_evidence_has_whole_paragraphs_ordered_gaps_and_local_full_chapter(temporal_run, tmp_path):
    book = load_json(temporal_run / "book.json")
    quote = next(q for q in load_json(temporal_run / "quotes.json") if q["quote_exact"].startswith("第二张票"))
    original = book["text"][quote["source"]["context_start"]:quote["source"]["context_end"]]
    assert "周澈把两张旧船票摊开" not in original  # Reproduces the old 180-character presentation gap.
    context = SourceContext(book).target(quote)
    assert len(context["passages"]) == 2 and context["omitted_ranges"]
    assert "周澈把两张旧船票摊开" in context["passages"][0]["text"]
    assert "此处都是听者的动作，没有人接过话头" in context["passages"][1]["text"]
    for passage in context["passages"]:
        assert passage["text"] == book["text"][passage["start"]:passage["end"]]
        paragraphs = [p for p in book["paragraphs"] if p["paragraph_id"] in passage["paragraph_ids"]]
        assert passage["start"] == paragraphs[0]["start"] and passage["end"] == paragraphs[-1]["end"]
    gap = context["omitted_ranges"][0]
    assert gap["characters"] == gap["end"]-gap["start"] > 0
    page = tmp_path / "private-review.html"
    targets = write_review_page(temporal_run, page, quotes_only=True)
    html = page.read_text(encoding="utf-8")
    raw = html.split('const review=', 1)[1].split(';\ndocument.getElementById', 1)[0]
    view = json.loads(raw)
    chapter = next(c for c in book["chapters"] if c["chapter_id"] == quote["source"]["chapter_id"])
    assert view["source_chapters"][chapter["chapter_id"]]["text"] == book["text"][chapter["start"]:chapter["end"]]
    assert 'delete result.source_chapters' in html and 'fetch(' not in html
    assert "查看完整段落与归属证据" in html and "这些上下文不是一条拼接引语" in html
    assert all(row["status"] == "pending" for row in targets["quotes"])
    assert len(targets["quotes"]) == 11  # No mistaken extra occurrence survives in the clean run.


def test_cross_chapter_evidence_is_separate_and_ordered_not_a_synthetic_continuous_quote(temporal_run):
    book = load_json(temporal_run / "book.json")
    quote = make_quote(book, "这一次，我会把灯带回去。")
    other = "今天的岸边，周澈说："
    start = book["text"].index(other)
    quote["speaker"]["evidence"] = [{"start": start, "end": start+len(other), "text": other}]
    target = SourceContext(book).target(quote)
    assert len(target["passages"]) == 2
    assert target["passages"][0]["chapter_id"] != target["passages"][1]["chapter_id"]
    assert target["passages"][1]["evidence_indexes"] == [0]
    assert quote["quote_exact"] == "这一次，我会把灯带回去。"
    # Structurally valid evidence is not proof of the semantic relationship.


@pytest.mark.parametrize("change", [{"start": True}, {"start": "1"}, {"end": 1.5}, {"text": 123},
                                        {"start": -1}, {"text": "invented evidence"}])
def test_invalid_or_fabricated_context_evidence_is_rejected(temporal_run, change):
    book = load_json(temporal_run / "book.json")
    quotes = load_json(temporal_run / "quotes.json")
    quotes[0]["speaker"]["evidence"][0].update(change)
    with pytest.raises(ValueError, match="evidence"):
        evidence(book, quotes[0]["speaker"]["evidence"][0])
    save_json(temporal_run / "quotes.json", quotes)
    with pytest.raises(ValueError, match="evidence"):
        review_targets(temporal_run, quotes_only=True)


@pytest.mark.parametrize("tamper", ["missing_policy", "missing_hash", "truncated_passage"])
def test_context_bound_approval_refuses_stale_or_truncated_material_without_partial_write(temporal_run, tamper):
    record = review_targets(temporal_run, quotes_only=True)
    record.update(reviewer="SIMULATED CONTEXT REVIEWER", attested=True, reviewed_at="2026-11-23T00:00:00Z")
    record["quotes"][0]["status"] = "approved"
    if tamper == "missing_policy":
        record["quotes"][0].pop("context_policy_version")
    elif tamper == "missing_hash":
        record["quotes"][0].pop("context_fingerprint")
    else:
        record["quotes"][0]["source_context"]["passages"][0]["text"] = "truncated"
    before = (temporal_run / "quotes.json").read_bytes()
    with pytest.raises(ValueError, match="source context changed or missing"):
        apply_review(temporal_run, record)
    assert (temporal_run / "quotes.json").read_bytes() == before


def test_semantic_error_can_be_source_exact_but_never_implicitly_approved_and_correction_invalidates(temporal_run, tmp_path):
    proposal = load_json(PROPOSAL)
    wrong = copy.deepcopy(proposal)
    wrong["quotes"][0]["speaker"].update(character_id="lin_che", explanation="DELIBERATE WRONG SPEAKER TEST")
    path = tmp_path / "wrong.json"
    save_json(path, wrong)
    apply_analysis_file(temporal_run, path)
    quotes = load_json(temporal_run / "quotes.json")
    affected = next(q for q in quotes if q["source"]["start"] == wrong["quotes"][0]["start"])
    assert affected["speaker"]["character_id"] == "lin_che" and affected["review"]["status"] == "pending"
    assert verify(load_json(temporal_run / "book.json"), affected)
    # Simulated human attestation only tests version invalidation, never real correctness.
    record = review_targets(temporal_run, quotes_only=True)
    record.update(reviewer="SIMULATED TEST HUMAN", attested=True, reviewed_at="2026-11-23T00:00:00Z")
    reviewed = next(row for row in record["quotes"] if row["quote_id"] == affected["quote_id"])
    reviewed["status"] = "approved"
    apply_review(temporal_run, record)
    apply_analysis_file(temporal_run, PROPOSAL)
    corrected = next(q for q in load_json(temporal_run / "quotes.json") if q["quote_id"] == affected["quote_id"])
    assert corrected["speaker"]["character_id"] == "wen_xue" and corrected["review"]["status"] == "pending"
    with pytest.raises(ValueError, match="invalidated"):
        apply_review(temporal_run, record)
    assert len(list((temporal_run / "analysis").glob("*.json"))) == 2


def test_zero_budget_local_ingest_resume_contract_and_reservation_stop(temporal_run):
    assert initialize_run(SOURCE, temporal_run, size=160, overlap=48, image_limit=0, resume=True)["reused"]
    request = contract_template(temporal_run)
    request.update(editor="SIMULATED LOCAL QA", reason="Explicit local-only zero-call task")
    request["definition"].update(character_ids=["lin_che"], allowed_destinations=["local"], input_rights_note="Self-authored fiction")
    assert request["definition"]["image_call_limit"] == sum(request["definition"]["image_call_allocation"].values()) == 0
    apply_contract(temporal_run, request)
    with Ledger(temporal_run / ".state/ledger.sqlite") as ledger:
        assert ledger.budget()["limit"] == ledger.budget()["reserved_calls"] == 0
        with pytest.raises(ValueError, match="budget exhausted"):
            ledger.reserve("SIMULATED_NEVER_INVOKED", "hash")
    with pytest.raises(ValueError, match="destination scope"):
        plan_image(temporal_run, {"call_id": "SIMULATED_BLOCKED", "asset_id": "SIMULATED", "kind": "reference",
            "tool": "image_gen.imagegen", "character_id": "lin_che", "prompt": "SIMULATED ONLY", "references": []})
    assert CONTEXT_POLICY_VERSION == 1


@pytest.mark.parametrize("change", ["index_only", "source_and_index"])
def test_full_chapter_materialization_refuses_source_change_after_targets(temporal_run, tmp_path, monkeypatch, change):
    from novel_quote_posters import review
    actual = review.review_targets

    def race(*args, **kwargs):
        targets = actual(*args, **kwargs)
        book = load_json(temporal_run / "book.json")
        if change == "index_only":
            book["text"] += "tampered cached text"
        else:
            source = temporal_run / "source.txt"
            source.write_text(book["text"]+"\n另一个来源版本。", encoding="utf-8")
            book = ingest(source, title=book["title"])
        save_json(temporal_run / "book.json", book)
        return targets

    monkeypatch.setattr(review, "review_targets", race)
    page = tmp_path / "must-not-publish.html"
    with pytest.raises(ValueError, match="source index differs|source changed"):
        write_review_page(temporal_run, page, quotes_only=True)
    assert not page.exists()
