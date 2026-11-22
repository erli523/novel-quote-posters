import pytest

from novel_quote_posters.ingest import index_text
from novel_quote_posters.quotes import make_quote, verify
from novel_quote_posters.source import validate_source
from novel_quote_posters.storage import load_json


def test_forged_cache_with_valid_self_hash_cannot_replace_original(poster_run):
    book = load_json(poster_run / "book.json")
    fake = index_text("第一章 伪造\n不存在的台词。", book["source_hash"], book["title"], format="txt")
    with pytest.raises(ValueError, match="differs from retained original"):
        validate_source(poster_run, fake)


def test_tampered_chapter_index_is_detected(poster_run):
    book = load_json(poster_run / "book.json")
    book["chapters"][0]["title"] = "虚构出处"
    with pytest.raises(ValueError, match="chapters"):
        validate_source(poster_run, book)


def test_quote_source_metadata_is_checked_not_just_the_text(poster_run):
    book = load_json(poster_run / "book.json")
    quote = make_quote(book, "路还在。")
    quote["source"]["chapter"] = "错误章节"
    assert not verify(book, quote)
