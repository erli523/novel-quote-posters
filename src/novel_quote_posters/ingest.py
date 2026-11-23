"""TXT/EPUB extraction. Coordinates are Unicode code points, never EPUB byte offsets."""

import posixpath
import re
import zipfile
from bisect import bisect_right
from pathlib import Path, PurePosixPath
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from .storage import sha256

LEGACY_CHAPTER = re.compile(
    r"(?m)^[ \t]*(?:第[零〇一二三四五六七八九十百千万两\d]+[章节卷回部][^\n]*"
    r"|chapter\s+\d+[^\n]*)[ \t]*$", re.IGNORECASE
)
CHAPTER = re.compile(
    r"(?m)^[ \t\u3000]*(?:第[零〇一二三四五六七八九十百千万两\d]+[章节卷回部](?=[ \t\u3000]|$)[^\n]*"
    r"|chapter\s+\d+[^\n]*)[ \t\u3000]*$", re.IGNORECASE
)
PARSER_VERSION = "ingest-2"
MAX_INPUT = 100 * 1024 * 1024


def decode_txt(raw: bytes, encoding: str | None = None) -> tuple[str, str]:
    if encoding:
        return raw.decode(encoding, errors="strict"), encoding
    codecs = ["utf-8-sig"]
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        codecs.append("utf-16")
    codecs.append("gb18030")
    for codec in codecs:
        try:
            value = raw.decode(codec, errors="strict")
            if "\x00" in value or "\ufffd" in value:
                continue
            return value, codec
        except UnicodeError:
            continue
    raise ValueError("cannot decode TXT without replacement; specify --encoding")


def _xml(data: bytes):
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("XML entities and DTD are not supported")
    return ET.fromstring(data)


def epub_text(raw: bytes) -> tuple[str, list[dict]]:
    import io

    sections, locations = [], []
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        if len(infos) > 10000 or sum(x.file_size for x in infos) > MAX_INPUT:
            raise ValueError("EPUB expanded size/member limit exceeded")
        names = [x.filename for x in infos]
        if len(set(names)) != len(names):
            raise ValueError("duplicate EPUB member")
        for name in names:
            if "\\" in name or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts:
                raise ValueError("unsafe EPUB member")
        container = _xml(archive.read("META-INF/container.xml"))
        package_name = next(
            e.attrib["full-path"] for e in container.iter() if e.tag.endswith("}rootfile")
        )
        if package_name not in names:
            raise ValueError("EPUB package missing")
        package = _xml(archive.read(package_name))
        manifest = {e.attrib["id"]: e.attrib for e in package.iter() if e.tag.endswith("}item")}
        spine = [e.attrib["idref"] for e in package.iter() if e.tag.endswith("}itemref")]
        if not spine:
            raise ValueError("EPUB spine is empty")
        cursor = 0
        for item_id in spine:
            item = manifest[item_id]
            member = posixpath.normpath(posixpath.join(
                posixpath.dirname(package_name), unquote(item["href"].split("#")[0])
            ))
            if member not in names or member.startswith("../"):
                raise ValueError("EPUB spine target missing or unsafe")
            document = _xml(archive.read(member))
            body = next((e for e in document.iter() if e.tag.rsplit("}", 1)[-1] == "body"), None)
            if body is None:
                raise ValueError("EPUB content has no body")

            def collect(element):
                tag = element.tag.rsplit("}", 1)[-1]
                if tag in {"script", "style"}:
                    return ""
                text = element.text or ""
                for child in element:
                    text += collect(child) + (child.tail or "")
                if tag in {"p", "div", "h1", "h2", "h3", "br", "li", "section"}:
                    text += "\n"
                return text

            section = collect(body).replace("\r\n", "\n").replace("\r", "\n").strip()
            if not section:
                raise ValueError(f"empty EPUB spine item: {member}")
            separator = "\n\n" if sections else ""
            cursor += len(separator)
            locations.append({"member": member, "start": cursor, "end": cursor + len(section)})
            sections.append(separator + section)
            cursor += len(section)
    return "".join(sections), locations


def index_text(text: str, source_hash: str, title: str, *, parser_version: str = PARSER_VERSION, **metadata) -> dict:
    if parser_version not in {"ingest-1", "ingest-2"}:
        raise ValueError("unsupported source parser version")
    headings = list((LEGACY_CHAPTER if parser_version == "ingest-1" else CHAPTER).finditer(text))
    heading_issues = []
    if parser_version == "ingest-2":
        unique = []
        for match in headings:
            if (unique and " ".join(unique[-1].group().split()) == " ".join(match.group().split())
                    and not text[unique[-1].end():match.start()].strip(" \t\n\u3000-=─_＿")):
                heading_issues.append({"type": "adjacent_repeated_heading", "start": match.start(),
                                       "end": match.end(), "retained_chapter_start": unique[-1].start(),
                                       "text_preserved": True})
            else:
                unique.append(match)
        headings = unique
    starts = [m.start() for m in headings]
    if not starts or starts[0] > 0:
        starts.insert(0, 0)
    chapters = []
    for number, start in enumerate(starts):
        end = starts[number + 1] if number + 1 < len(starts) else len(text)
        heading = next((m.group().strip() for m in headings if m.start() == start), "前言/正文")
        if text[start:end].strip():
            chapters.append({"chapter_id": f"ch{len(chapters)+1:04d}", "title": heading,
                             "start": start, "end": end})
    paragraphs = []
    chapter_starts = [c["start"] for c in chapters]
    for match in re.finditer(r"[^\n]+", text):
        if not match.group().strip():
            continue
        chapter = chapters[bisect_right(chapter_starts, match.start())-1]
        paragraphs.append({"paragraph_id": f"p{len(paragraphs)+1:06d}",
                           "chapter_id": chapter["chapter_id"],
                           "start": match.start(), "end": match.end()})
    return {"schema_version": 1, "parser_version": parser_version,
            "book_id": source_hash[:16], "title": title, "source_hash": source_hash,
            "canonical_hash": sha256(text.encode()), "coordinate_unit": "unicode_codepoint",
            "interval": "half_open", "text": text, "chapters": chapters,
            "paragraphs": paragraphs, **({"heading_issues": heading_issues} if parser_version == "ingest-2" else {}), **metadata}


def ingest(path: Path, title: str | None = None, encoding: str | None = None,
           *, parser_version: str = PARSER_VERSION) -> dict:
    if path.stat().st_size > MAX_INPUT:
        raise ValueError("input exceeds 100 MiB limit")
    raw = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix == ".txt":
        text, codec = decode_txt(raw, encoding)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        locations = []
    elif suffix == ".epub":
        text, locations = epub_text(raw)
        codec = "XML declared encoding"
    else:
        raise ValueError("V1 currently supports TXT and EPUB only; DOCX/PDF/OCR not implemented")
    if not text.strip():
        raise ValueError("empty book")
    return index_text(text, sha256(raw), title or path.stem, parser_version=parser_version, format=suffix[1:], encoding=codec,
                      transformations=["CRLF/CR to LF", "EPUB block text extraction" if locations
                                       else "TXT decoded without replacement"],
                      container_locations=locations)


def chunks(book: dict, size: int = 6000, overlap: int = 300) -> list[dict]:
    if not 0 <= overlap < size:
        raise ValueError("require 0 <= overlap < size")
    result, start = [], 0
    while start < len(book["text"]):
        end = min(start + size, len(book["text"]))
        result.append({"chunk_id": f"chunk{len(result)+1:05d}", "start": start, "end": end,
                       "sha256": sha256(book["text"][start:end].encode()),
                       "semantic_status": "pending"})
        if end == len(book["text"]):
            break
        start = end - overlap
    return result
