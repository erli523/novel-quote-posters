"""Deterministic helpers for the application coordinator. No embedded image API."""

import argparse
import shutil
from pathlib import Path

from .ingest import chunks, ingest
from .quotes import candidates
from .storage import save_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("ingest", help="index TXT/EPUB and discover unreviewed quotations")
    command.add_argument("source", type=Path)
    command.add_argument("--run", type=Path, required=True)
    command.add_argument("--title")
    command.add_argument("--encoding")
    command.add_argument("--chunk-size", type=int, default=6000)
    command.add_argument("--overlap", type=int, default=300)
    args = parser.parse_args()
    try:
        if args.command == "ingest":
            if args.run.exists():
                raise ValueError("run already exists; use a new run ID to preserve evidence")
            book = ingest(args.source, args.title, args.encoding)
            windows = chunks(book, args.chunk_size, args.overlap)
            quotes, issues = candidates(book)
            args.run.mkdir(parents=True)
            source = args.run / ("source" + args.source.suffix.lower())
            shutil.copyfile(args.source, source)
            save_json(args.run / "book.json", book)
            save_json(args.run / "chunks.json", windows)
            save_json(args.run / "quotes.json", quotes)
            save_json(args.run / "ingest-report.json", {
                "source_hash": book["source_hash"], "chapters_indexed": len(book["chapters"]),
                "characters_indexed": len(book["text"]), "chunks_indexed": len(windows),
                "semantic_chunks_processed": 0, "candidates": len(quotes), "issues": issues,
                "speaker_review": "pending", "coverage_claim": "index_only"})
            print(f"Indexed {len(book['chapters'])} chapters; {len(quotes)} unreviewed candidates.")
    except (ValueError, OSError, KeyError) as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    main()
