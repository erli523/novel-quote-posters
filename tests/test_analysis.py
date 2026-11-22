import pytest

from novel_quote_posters.analysis import apply_analysis
from novel_quote_posters.ingest import chunks, index_text


def example():
    source = index_text("第一章 测试\n林澈说：“路还在。”", "test", "测试")
    text = "林澈说："
    start = source["text"].index(text)
    windows = chunks(source)
    proposal = {"source_hash": "test", "processed_chunks": [
        {**windows[0], "summary": "林澈明确说话"}],
        "characters": {"lin_che": {"character_id": "lin_che", "name": "林澈", "traits": []}},
        "quotes": [{"quote_exact": "路还在。", "start": source["text"].index("路还在。"),
                    "quote_type": "dialogue", "speaker": {"status": "explicit",
                    "character_id": "lin_che", "evidence": [
                        {"start": start, "end": start+len(text), "text": text}]}}]}
    return source, windows, proposal


def test_valid_analysis_never_grants_human_approval():
    source, windows, proposal = example()
    quotes, characters, updated = apply_analysis(source, windows, proposal)
    assert quotes[0]["review"]["status"] == "pending"
    assert quotes[0]["speaker"]["character_id"] in characters
    assert updated[0]["semantic_status"] == "application_processed"


def test_hallucinated_evidence_and_stale_chunks_are_rejected():
    source, windows, proposal = example()
    proposal["quotes"][0]["speaker"]["evidence"][0]["text"] = "闻雪说："
    with pytest.raises(ValueError, match="evidence"):
        apply_analysis(source, windows, proposal)
    source, windows, proposal = example()
    proposal["processed_chunks"][0]["sha256"] = "old"
    with pytest.raises(ValueError, match="stale"):
        apply_analysis(source, windows, proposal)


def test_incremental_analysis_preserves_previously_processed_windows():
    source, windows, proposal = example()
    windows[0]["semantic_status"] = "application_processed"
    proposal["processed_chunks"] = []
    _, _, updated = apply_analysis(source, windows, proposal)
    assert updated[0]["semantic_status"] == "application_processed"


def test_narration_cannot_masquerade_as_named_dialogue():
    source, windows, proposal = example()
    proposal["quotes"][0]["quote_type"] = "narration"
    with pytest.raises(ValueError, match="consistently"):
        apply_analysis(source, windows, proposal)


def test_named_attribution_requires_an_actual_character():
    source, windows, proposal = example()
    proposal["quotes"][0]["speaker"]["character_id"] = None
    with pytest.raises(ValueError, match="needs a character"):
        apply_analysis(source, windows, proposal)
