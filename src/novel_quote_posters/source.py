"""Bind cached indexes back to the retained original file, not merely its claimed hash."""

from pathlib import Path

from .ingest import ingest
from .storage import contained, digest_file


def validate_source(run: Path, book: dict) -> None:
    path = contained(run, "source." + book["format"])
    if digest_file(path) != book["source_hash"]:
        raise ValueError("source file hash changed")
    encoding = book.get("encoding") if book["format"] == "txt" else None
    actual = ingest(path, title=book["title"], encoding=encoding, parser_version=book["parser_version"])
    for key in ["source_hash", "canonical_hash", "text", "chapters", "paragraphs",
                "parser_version", "coordinate_unit", "interval"]:
        if actual[key] != book[key]:
            raise ValueError(f"cached source index differs from retained original: {key}")
    if book["parser_version"] == "ingest-2" and actual["heading_issues"] != book.get("heading_issues"):
        raise ValueError("cached source heading diagnostics differ from retained original")
