"""Self-authored PG-shaped data only; actual complete books stay out of the repository."""

import pytest

from novel_quote_posters.ingest import index_text, ingest
from novel_quote_posters.public_domain import (
    audit_chapters,
    chapter_number,
    gutenberg_body,
    wikisource_body,
)
from novel_quote_posters.quotes import QuoteIndex, candidates, locate
from novel_quote_posters.source import validate_source
from novel_quote_posters.storage import save_json, sha256


def test_lossless_body_range_keeps_every_book_character_and_retains_original_mapping():
    text = '\ufeffPublisher header\r\n*** START OF THIS PROJECT GUTENBERG EBOOK TEST ***\r\n\r\n第一章 测试\r\n　　甲说：“保留全角缩进。”\r\n*** END OF THIS PROJECT GUTENBERG EBOOK TEST ***\r\nLicense\r\n'
    raw = text.encode()
    data, mapping = gutenberg_body(raw)
    normalized = raw.decode("utf-8-sig").replace("\r\n", "\n")
    assert data.decode() == normalized[mapping["normalized_original_start"]:mapping["normalized_original_end"]]
    assert '　　甲说：“保留全角缩进。”' in data.decode()
    assert "License" not in data.decode() and mapping["original_sha256"] == sha256(raw)
    assert mapping["body_text_changed"] is False


@pytest.mark.parametrize("text", [
    "missing markers", "*** START OF THIS PROJECT GUTENBERG EBOOK TEST ***\n第一章 测试",
    "*** END OF THIS PROJECT GUTENBERG EBOOK TEST ***\n*** START OF THIS PROJECT GUTENBERG EBOOK TEST ***",
    "*** START OF THIS PROJECT GUTENBERG EBOOK TEST ***\n*** END OF THIS PROJECT GUTENBERG EBOOK TEST ***",
    "*** START OF THIS PROJECT GUTENBERG EBOOK TEST ***\n*** START OF THIS PROJECT GUTENBERG EBOOK TEST ***\ntext\n*** END OF THIS PROJECT GUTENBERG EBOOK TEST ***",
])
def test_truncated_ambiguous_or_empty_sources_are_rejected(text):
    with pytest.raises(ValueError):
        gutenberg_body(text.encode())


def test_new_parser_recognizes_fullwidth_indent_and_rejects_body_references_without_altering_text():
    text = '第一回 起始\n甲说：“开船。”\n　　第二回 转折\n第一回中已经说明，此句是正文。\n第二回了，你还没听清吗？\n　　第三回 结束\n乙说：“靠岸。”'
    book = index_text(text, "test", "self-authored")
    assert book["text"] == text and len(book["chapters"]) == 3
    assert audit_chapters(book, [1, 2, 3], unit="回")["sequence_matches"]
    legacy = index_text(text, "test", "self-authored", parser_version="ingest-1")
    assert legacy["chapters"] != book["chapters"]
    assert legacy["text"] == book["text"]


def test_adjacent_repeated_heading_is_reported_and_preserved_while_repeated_content_is_not_hidden():
    text = "第一回 开篇\n甲出发。\n第二回 抵达\n----------------\n\n第二回 抵达\n乙接船。\n第三回 归程\n丙回家。"
    book = index_text(text, "test", "self-authored")
    result = audit_chapters(book, [1, 2, 3], unit="回")
    assert result["sequence_matches"] and len(result["heading_issues"]) == 1
    assert book["text"].count("第二回 抵达") == 2 and result["heading_issues"][0]["text_preserved"]
    malformed = index_text(text+"\n第三回 归程\n丙回家。", "test", "self-authored")
    result = audit_chapters(malformed, [1, 2, 3], unit="回")
    assert not result["sequence_matches"] and result["duplicates"] == [3]
    missing = audit_chapters(book, [1, 2, 3, 4], unit="回")
    assert missing["missing"] == [4] and not missing["sequence_matches"]


@pytest.mark.parametrize(("text", "number"), [("九", 9), ("十二", 12), ("五十六", 56), ("一百二十", 120),
                                          ("一零零", 100), ("一一九", 119), ("一二零", 120), ("１２", 12)])
def test_source_chapter_number_forms(text, number):
    assert chapter_number(text) == number


def test_legacy_run_source_validation_dispatches_to_its_recorded_parser(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("第一回 开始\n甲说：“出发。”\n　　第二回 到达\n乙说：“靠岸。”", encoding="utf-8")
    book = ingest(source, parser_version="ingest-1")
    save_json(tmp_path / "book.json", book)
    validate_source(tmp_path, book)
    assert len(book["chapters"]) == 1 and len(ingest(source)["chapters"]) == 2
    book["parser_version"] = "future-unknown"
    with pytest.raises(ValueError, match="unsupported source parser"):
        validate_source(tmp_path, book)


def test_lookup_index_matches_linear_source_paragraphs_for_all_extracted_quotes():
    text = '第一章 测试\n甲说：“短句。”\n乙说：“跨段\n继续。”\n第二章 结尾\n丙说：“短句。”'
    book = index_text(text, "test", "self-authored")
    quotes, _ = candidates(book)
    index = QuoteIndex(book)
    assert len(quotes) == 3
    for quote in quotes:
        source = quote["source"]
        assert source == locate(book, quote["quote_exact"], source["start"], index=index)
        assert source["paragraph_ids"] == [p["paragraph_id"] for p in book["paragraphs"]
                                           if p["start"] < source["end"] and source["start"] < p["end"]]
    with pytest.raises(ValueError, match="another book"):
        locate({**book}, "短句。", quotes[0]["source"]["start"], index=index)


def test_explicit_wikisource_corpus_preparation_keeps_book_text_and_excludes_page_chrome():
    html = '<div><p>Author navigation</p><div class="prp-pages-output"><h2>第一章 测试</h2><p>甲说：<span>“路&amp;灯。”</span><span class="pagenum">123</span></p><script>upload secrets</script><p>下一段<br/>继续。</p><span class="mw-editsection">edit link</span></div><p>License footer retained in original only</p></div>'
    data, mapping = wikisource_body(html)
    assert data.decode() == '第一章 测试\n甲说：“路&灯。”\n下一段\n继续。\n'
    assert mapping["coordinate_unit"] == "derived_unicode_codepoint_not_html_byte_offset"
    assert not any(t in data.decode() for t in ["navigation", "123", "upload", "edit link", "License"])


@pytest.mark.parametrize("html", ['<p>no container</p>', '<div class="prp-pages-output"><p>cut off',
                                     '<div class="prp-pages-output"></div>',
                                     '<div class="prp-pages-output">one</div><div class="prp-pages-output">two</div>'])
def test_wikisource_corpus_preparation_refuses_missing_partial_or_ambiguous_scope(html):
    with pytest.raises(ValueError):
        wikisource_body(html)
