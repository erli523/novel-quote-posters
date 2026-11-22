"""Deterministic helpers for the application coordinator. No embedded image API."""

import argparse
import shutil
from pathlib import Path

from .analysis import apply_analysis
from .documents import commit_documents, recover_documents
from .export import export_bundle
from .ingest import chunks, ingest
from .layout import STYLES
from .quotes import candidates
from .rendering import render_cards
from .review import apply_review, write_review_page
from .source import validate_source
from .storage import digest_file, load_json, save_json


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
    command = commands.add_parser("render", help="compose imported card data; draft unless approved")
    command.add_argument("--run", type=Path, required=True)
    command.add_argument("--font", type=Path, required=True)
    command.add_argument("--card", help="render just one card ID")
    command.add_argument("--width", type=int, default=1080)
    command.add_argument("--height", type=int, default=1440)
    command.add_argument("--clean-candidate", action="store_true",
                         help="omit draft label for human inspection; does not grant approval")
    command = commands.add_parser("apply-analysis", help="validate application-side analysis JSON")
    command.add_argument("proposal", type=Path)
    command.add_argument("--run", type=Path, required=True)
    command = commands.add_parser("export", help="validate and package PNG/CSV/JSON")
    command.add_argument("--run", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--draft", action="store_true", help="requires visible pending-review labels")
    command.add_argument("--allow-rewrite", action="store_true")
    command = commands.add_parser("review", help="write a private offline human-review page")
    command.add_argument("--run", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--quotes-only", action="store_true")
    command = commands.add_parser("apply-review", help="validate and import actual human decisions")
    command.add_argument("record", type=Path)
    command.add_argument("--run", type=Path, required=True)
    command = commands.add_parser("recover", help="finish an interrupted JSON transaction")
    command.add_argument("--run", type=Path, required=True)
    commands.add_parser("styles", help="print built-in, editable layout parameters")
    args = parser.parse_args()
    try:
        if args.command not in {"ingest", "styles", "recover"}:
            recover_documents(args.run)
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
        elif args.command == "styles":
            import json
            print(json.dumps(STYLES, ensure_ascii=False, indent=2))
        elif args.command == "render":
            report = render_cards(args.run, args.font, card_id=args.card, width=args.width,
                                  height=args.height, draft=not args.clean_candidate)
            print(f"Rendered {report['rendered']}; reused {report['reused']}; image calls 0.")
        elif args.command == "export":
            report = export_bundle(args.run, args.output, draft=args.draft,
                                   allow_rewrite=args.allow_rewrite)
            print(f"Exported {len(report['assets'])} cards; draft={report['draft']}")
        elif args.command == "apply-analysis":
            book = load_json(args.run / "book.json")
            validate_source(args.run, book)
            windows = load_json(args.run / "chunks.json")
            proposal = load_json(args.proposal)
            quotes, characters, updated = apply_analysis(book, windows, proposal)
            snapshot = args.run / "analysis" / (digest_file(args.proposal) + ".json")
            if not snapshot.exists():
                snapshot.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(args.proposal, snapshot)
            elif digest_file(snapshot) != digest_file(args.proposal):
                raise ValueError("analysis snapshot checksum mismatch")
            existing = {q["quote_id"]: q for q in load_json(args.run / "quotes.json")}
            for quote in quotes:
                previous = existing.get(quote["quote_id"])
                if previous and {k: v for k, v in previous.items() if k != "review"} == {
                        k: v for k, v in quote.items() if k != "review"}:
                    quote["review"] = previous["review"]
                existing[quote["quote_id"]] = quote
            old_characters = load_json(args.run / "characters.json") if (args.run / "characters.json").exists() else {}
            report = {
                "source_hash": book["source_hash"], "proposal_hash": digest_file(args.proposal),
                "semantic_chunks_processed": sum(w["semantic_status"] == "application_processed"
                                                  for w in updated),
                "chunks_total": len(updated), "quotes": len(quotes),
                "human_review": "pending", "reviewer_type": "application_assistant"}
            commit_documents(args.run, {"quotes.json": list(existing.values()),
                                       "characters.json": {**old_characters, **characters},
                                       "chunks.json": updated, "analysis-report.json": report},
                             reason="application_analysis")
            print(f"Validated {len(quotes)} semantic proposals; human review pending.")
        elif args.command == "review":
            report = write_review_page(args.run, args.output, quotes_only=args.quotes_only)
            print(f"Review page: {len(report['quotes'])} quotes, {len(report['cards'])} cards; no approval granted.")
        elif args.command == "apply-review":
            report = apply_review(args.run, load_json(args.record))
            print(f"Imported human decisions: {report['quotes_changed']} quotes, {report['cards_changed']} cards.")
        elif args.command == "recover":
            print("Recovered pending transaction." if recover_documents(args.run) else "No pending transaction.")
    except (ValueError, OSError, KeyError) as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    main()
