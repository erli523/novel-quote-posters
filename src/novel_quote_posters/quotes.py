"""Mechanical candidate discovery and exact source checks; semantics stay reviewable."""

from bisect import bisect_left, bisect_right

from .storage import sha256


class QuoteIndex:
    """Per-operation lookup index; never globally cache mutable book dictionaries."""
    def __init__(self, book: dict):
        self.book = book
        self.chapter_starts = [c["start"] for c in book["chapters"]]
        self.paragraph_starts = [p["start"] for p in book["paragraphs"]]
        self.paragraph_ends = [p["end"] for p in book["paragraphs"]]


def locate(book: dict, text: str, start: int | None = None, *, index: QuoteIndex | None = None) -> dict:
    if not text:
        raise ValueError("empty quote")
    if start is None:
        positions, offset = [], 0
        while (position := book["text"].find(text, offset)) >= 0:
            positions.append(position)
            offset = position + 1
        if len(positions) != 1:
            raise ValueError(f"quote has {len(positions)} exact occurrences; specify start")
        start = positions[0]
    if type(start) is not int:
        raise ValueError("quote start must be an integer codepoint position")
    end = start + len(text)
    if start < 0 or book["text"][start:end] != text:
        raise ValueError("quote is not an exact continuous source span")
    index = index or QuoteIndex(book)
    if index.book is not book:
        raise ValueError("quote lookup index belongs to another book")
    chapter = book["chapters"][bisect_right(index.chapter_starts, start)-1]
    if end > chapter["end"]:
        raise ValueError("quote crosses chapter boundary")
    first = bisect_right(index.paragraph_ends, start)
    last = bisect_left(index.paragraph_starts, end)
    paragraphs = [p["paragraph_id"] for p in book["paragraphs"][first:last]]
    return {"source_hash": book["source_hash"], "canonical_hash": book["canonical_hash"],
            "start": start, "end": end, "chapter_id": chapter["chapter_id"],
            "chapter": chapter["title"], "paragraph_ids": paragraphs,
            "context_start": max(chapter["start"], start - 180),
            "context_end": min(chapter["end"], end + 180)}


def verify(book: dict, quote: dict) -> bool:
    source = quote["source"]
    exact = (source["source_hash"] == book["source_hash"]
            and source["canonical_hash"] == book["canonical_hash"]
            and sha256(book["text"].encode()) == book["canonical_hash"]
            and 0 <= source["start"] < source["end"] <= len(book["text"])
            and book["text"][source["start"]:source["end"]] == quote["quote_exact"])
    if not exact:
        return False
    try:
        return source == locate(book, quote["quote_exact"], source["start"])
    except ValueError:
        return False


def make_quote(book: dict, text: str, start: int | None = None, *, index: QuoteIndex | None = None) -> dict:
    source = locate(book, text, start, index=index)
    quote_id = "q_" + sha256(f'{book["source_hash"]}:{source["start"]}:{source["end"]}'.encode())[:16]
    return {"quote_id": quote_id, "quote_exact": text, "display_text": text,
            "text_mode": "original", "source": source, "exact_verified": True,
            "speaker": {"status": "ambiguous", "character_id": None, "evidence": []},
            "quote_type": "unclassified", "review": {"status": "pending"},
            "selection": {"score": None, "reason": None, "origin": "mechanical_candidate"}}


def candidates(book: dict) -> tuple[list[dict], list[dict]]:
    """Match Chinese quote pairs, including multiline; leave attribution unapproved."""
    result, issues, stack = [], [], []
    lookup = QuoteIndex(book)
    pairs = {"“": "”", "「": "」", "『": "』"}
    for chapter in book["chapters"]:
        stack.clear()
        for index in range(chapter["start"], chapter["end"]):
            char = book["text"][index]
            if char in pairs:
                stack.append((index, pairs[char]))
            elif char in pairs.values():
                if stack and stack[-1][1] == char:
                    start, _ = stack.pop()
                    if not stack and index > start + 1:
                        result.append(make_quote(book, book["text"][start+1:index], start+1, index=lookup))
                else:
                    issues.append({"type": "unmatched_close", "offset": index})
        issues.extend({"type": "unclosed_quote", "offset": start} for start, _ in stack)
    return result, issues


def check_display(book: dict, quote: dict, allow_rewrite: bool = False) -> None:
    if not verify(book, quote):
        raise ValueError("source verification failed")
    if quote["text_mode"] == "original":
        if quote["display_text"] != quote["quote_exact"]:
            raise ValueError("changed display text must be marked rewrite")
    elif quote["text_mode"] != "rewrite" or not allow_rewrite:
        raise ValueError("rewrite is not permitted by this task")
