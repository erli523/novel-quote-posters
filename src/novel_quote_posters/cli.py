"""Deterministic helpers for the application coordinator. No embedded image API."""

import argparse
import shutil
from pathlib import Path

from .analysis import apply_analysis
from .export import export_bundle
from .ingest import chunks, ingest
from .layout import STYLES, compose
from .quotes import candidates
from .storage import contained, digest_file, load_json, save_json


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
    commands.add_parser("styles", help="print built-in, editable layout parameters")
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
        elif args.command == "styles":
            import json
            print(json.dumps(STYLES, ensure_ascii=False, indent=2))
        elif args.command == "render":
            book = load_json(args.run / "book.json")
            quotes = {q["quote_id"]: q for q in load_json(args.run / "quotes.json")}
            characters = load_json(args.run / "characters.json")
            cards = load_json(args.run / "cards.json")
            selected = [c for c in cards if not args.card or c["card_id"] == args.card]
            if not selected:
                raise ValueError("no matching cards")
            for card in selected:
                quote = quotes[card["quote_id"]]
                from .quotes import check_display
                check_display(book, quote, allow_rewrite=True)
                output = contained(args.run, card["poster"])
                if output.exists():
                    raise ValueError("poster exists; version its path before re-rendering")
                compose(contained(args.run, card["background"]), output,
                        text=quote["display_text"], character=characters[card["character_id"]]["name"],
                        title=book["title"], chapter=quote["source"]["chapter"], font_path=args.font,
                        style_id=card["style_id"], width=args.width, height=args.height,
                        rewrite=quote["text_mode"] == "rewrite", draft=not args.clean_candidate,
                        overrides=card.get("layout_overrides"))
                print(f"Rendered draft {card['card_id']}")
        elif args.command == "export":
            report = export_bundle(args.run, args.output, draft=args.draft,
                                   allow_rewrite=args.allow_rewrite)
            print(f"Exported {len(report['assets'])} cards; draft={report['draft']}")
        elif args.command == "apply-analysis":
            book = load_json(args.run / "book.json")
            windows = load_json(args.run / "chunks.json")
            proposal = load_json(args.proposal)
            quotes, characters, updated = apply_analysis(book, windows, proposal)
            snapshot = args.run / "analysis" / (digest_file(args.proposal) + ".json")
            save_json(snapshot, proposal)
            save_json(args.run / "quotes.json", quotes)
            save_json(args.run / "characters.json", characters)
            save_json(args.run / "chunks.json", updated)
            save_json(args.run / "analysis-report.json", {
                "source_hash": book["source_hash"], "proposal_hash": digest_file(args.proposal),
                "semantic_chunks_processed": sum(w["semantic_status"] == "application_processed"
                                                  for w in updated),
                "chunks_total": len(updated), "quotes": len(quotes),
                "human_review": "pending", "reviewer_type": "application_assistant"})
            print(f"Validated {len(quotes)} semantic proposals; human review pending.")
    except (ValueError, OSError, KeyError) as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    main()
