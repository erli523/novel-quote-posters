"""Lossless body ranges and declared chapter audits for locally retained public books."""

import re
from collections import Counter
from html.parser import HTMLParser

from .storage import sha256

START = re.compile(r"(?m)^\*\*\* START OF (?:THIS|THE) PROJECT GUTENBERG EBOOK [^\n]+\*\*\*[ \t]*$")
END = re.compile(r"(?m)^\*\*\* END OF (?:THIS|THE) PROJECT GUTENBERG EBOOK [^\n]+\*\*\*[ \t]*$")
NUMBER = re.compile(r"^第([零〇一二三四五六七八九十百千万两\d]+)([章节卷回部])(?:\s|$)")
DIGITS = dict(zip("零〇一二三四五六七八九两", [0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 2], strict=True))


def wikisource_chapter_body(html: str, expected_number: int) -> tuple[bytes, dict]:
    """Prepare a declared Novel-template chapter, retaining its source header title.

    Separate from the transcluded-pages extractor: no general HTML import promise.
    The returned coordinates address derived text, never HTML byte positions.
    """
    if type(expected_number) is not int or expected_number <= 0:
        raise ValueError("declare a positive expected chapter number")

    class Extractor(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack, self.parts, self.cells = [], [], []
            self.roots, self.headers, self.transclusions = 0, 0, 0

        def handle_starttag(self, tag, attrs):
            classes = set(dict(attrs).get("class", "").split())
            parent = self.stack[-1] if self.stack else {}
            root = "mw-parser-output" in classes
            self.roots += int(root)
            active = root or parent.get("active", False)
            blocked = parent.get("blocked", False) or tag in {"script", "style"} or bool(
                classes & {"noprint", "ws-noexport", "ws-footer", "mw-editsection", "pagenum", "ws-pagenum"})
            header = active and ("ws-header" in classes or parent.get("header", False))
            self.headers += int(active and "ws-header" in classes)
            self.transclusions += int(active and "prp-pages-output" in classes)
            cell = [] if tag == "td" and header and not blocked else parent.get("cell")
            if tag == "td" and cell is not None:
                self.cells.append(cell)
            if tag == "br" and active and not blocked and not header:
                self.parts.append("\n")
            if tag not in {"br", "img", "hr", "meta", "link", "input", "wbr", "source", "area", "embed", "param", "track", "col", "base"}:
                self.stack.append({"tag": tag, "active": active, "blocked": blocked, "header": header, "cell": cell})

        def handle_endtag(self, tag):
            for index in range(len(self.stack)-1, -1, -1):
                frame = self.stack[index]
                if frame["tag"] == tag:
                    if (frame["active"] and not frame["blocked"] and not frame["header"]
                            and tag in {"p", "div", "h1", "h2", "h3", "h4", "dd", "dt", "li", "blockquote", "pre", "section"}):
                        self.parts.append("\n")
                    del self.stack[index:]
                    break

        def handle_data(self, data):
            if not self.stack:
                return
            frame = self.stack[-1]
            if frame["active"] and not frame["blocked"]:
                if frame["header"] and frame["cell"] is not None:
                    frame["cell"].append(data)
                elif not frame["header"]:
                    self.parts.append(data)

    parser = Extractor()
    parser.feed(html)
    parser.close()
    if (parser.roots != 1 or not parser.headers or parser.transclusions
            or any(f["active"] for f in parser.stack)):
        raise ValueError("one complete Wikisource Novel chapter container with source header required")
    titles = [" ".join("".join(cell).split()) for cell in parser.cells]
    titles = [(value, NUMBER.match(value)) for value in titles if NUMBER.match(value)]
    if (len(titles) != 1 or titles[0][1][2] != "回"
            or chapter_number(titles[0][1][1]) != expected_number):
        raise ValueError("source chapter header differs from declared chapter number")
    title = titles[0][0]
    body = "".join(parser.parts).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not body:
        raise ValueError("empty Wikisource chapter body")
    data = (title+"\n\n"+body+"\n").encode()
    return data, {"schema_version": 1, "extractor": "wikisource-novel-chapter-1",
                  "html_sha256": sha256(html.encode()), "body_sha256": sha256(data),
                  "source_header_title": title, "declared_chapter_number": expected_number,
                  "text_body_start": len(title)+2,
                  "coordinate_unit": "derived_unicode_codepoint_not_html_byte_offset",
                  "transformations": ["one mw-parser-output Novel-template chapter",
                                      "source header title whitespace collapsed into one heading line",
                                      "body text nodes and block/br line breaks; HTML entities decoded",
                                      "navigation/headers/footers/page labels/scripts/styles excluded",
                                      "outer body whitespace trimmed; source wording retained"],
                  "transcription_accuracy": "requires_source_and_human_review"}


def wikisource_body(html: str) -> tuple[bytes, dict]:
    """Explicit corpus preparation, not general HTML/PDF input support or OCR."""
    class Extractor(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack, self.parts, self.roots = [], [], 0

        def handle_starttag(self, tag, attrs):
            classes = set(dict(attrs).get("class", "").split())
            parent_active, parent_blocked = self.stack[-1][1:] if self.stack else (False, False)
            root = "prp-pages-output" in classes
            self.roots += int(root)
            active = root or parent_active
            blocked = parent_blocked or tag in {"script", "style"} or bool(
                classes & {"pagenum", "ws-pagenum", "mw-editsection", "noprint", "ws-noexport"})
            if tag == "br" and active and not blocked:
                self.parts.append("\n")
            if tag not in {"br", "img", "hr", "meta", "link", "input", "wbr", "source", "area", "embed", "param", "track", "col", "base"}:
                self.stack.append((tag, active, blocked))

        def handle_endtag(self, tag):
            for index in range(len(self.stack)-1, -1, -1):
                if self.stack[index][0] == tag:
                    _, active, blocked = self.stack[index]
                    if active and not blocked and tag in {"p", "div", "h1", "h2", "h3", "h4", "li"}:
                        self.parts.append("\n")
                    del self.stack[index:]
                    break

        def handle_data(self, data):
            if self.stack and self.stack[-1][1] and not self.stack[-1][2]:
                self.parts.append(data)

    parser = Extractor()
    parser.feed(html)
    parser.close()
    if parser.roots != 1 or any(active for _, active, _ in parser.stack):
        raise ValueError("one complete Wikisource transcluded-pages container required")
    text = "".join(parser.parts).strip()+"\n"
    if not text.strip():
        raise ValueError("empty Wikisource source body")
    data = text.encode()
    return data, {"schema_version": 1, "extractor": "wikisource-transcluded-text-1",
                  "html_sha256": sha256(html.encode()), "body_sha256": sha256(data),
                  "coordinate_unit": "derived_unicode_codepoint_not_html_byte_offset",
                  "transformations": ["transcluded prp-pages-output text nodes only", "HTML entities decoded",
                                      "block/br line breaks retained", "navigation/page labels/scripts/styles excluded",
                                      "outer whitespace trimmed"],
                  "transcription_accuracy": "requires_source_and_human_review"}


def gutenberg_body(raw: bytes) -> tuple[bytes, dict]:
    text = raw.decode("utf-8-sig", errors="strict").replace("\r\n", "\n").replace("\r", "\n")
    starts, ends = list(START.finditer(text)), list(END.finditer(text))
    if len(starts) != 1 or len(ends) != 1 or starts[0].end() >= ends[0].start():
        raise ValueError("one ordered Gutenberg start/end pair required; do not accept a truncated book")
    start, end = starts[0].end(), ends[0].start()
    if text[start:start+1] == "\n":
        start += 1
    body = text[start:end]
    if not body.strip():
        raise ValueError("empty Gutenberg body")
    data = body.encode("utf-8")
    return data, {"schema_version": 1, "extractor": "gutenberg-body-1", "original_sha256": sha256(raw),
                  "normalized_original_sha256": sha256(text.encode()), "body_sha256": sha256(data),
                  "normalized_original_start": start, "normalized_original_end": end,
                  "coordinate_unit": "unicode_codepoint", "interval": "half_open",
                  "transformations": ["strict UTF-8/BOM decoding", "CRLF/CR to LF", "retained exact range between PG markers"],
                  "body_text_changed": False, "original_and_license_retained": True}


def chapter_number(value: str) -> int:
    if value.isdecimal():
        return int(value)
    if not any(c in "十百千万" for c in value):
        return int("".join(str(DIGITS[c]) for c in value))
    total, section, digit = 0, 0, 0
    for char in value:
        if char in DIGITS:
            digit = DIGITS[char]
        elif char == "万":
            total += (section+digit)*10000
            section, digit = 0, 0
        else:
            section += (digit or 1)*{"十": 10, "百": 100, "千": 1000}[char]
            digit = 0
    return total+section+digit


def audit_chapters(book: dict, expected: list[int], *, unit: str) -> dict:
    if not expected or any(type(n) is not int or n <= 0 for n in expected) or len(set(expected)) != len(expected):
        raise ValueError("declare the expected complete work chapter sequence explicitly")
    rows = []
    for chapter in book["chapters"]:
        match = NUMBER.match(chapter["title"])
        if match and match[2] == unit:
            rows.append({"number": chapter_number(match[1]), "chapter_id": chapter["chapter_id"],
                         "start": chapter["start"], "end": chapter["end"]})
    actual = [r["number"] for r in rows]
    counts = Counter(actual)
    return {"schema_version": 1, "scope": "declared_heading_sequence_not_semantic_or_transcription_accuracy",
            "expected": expected, "actual": actual, "sequence_matches": actual == expected,
            "missing": sorted(set(expected)-set(actual)), "unexpected": sorted(set(actual)-set(expected)),
            "duplicates": sorted(n for n, count in counts.items() if count > 1), "chapters": rows,
            "other_sections": len(book["chapters"])-len(rows), "heading_issues": book.get("heading_issues", [])}
