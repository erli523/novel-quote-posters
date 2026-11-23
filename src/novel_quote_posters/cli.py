"""Deterministic helpers for the application coordinator. No embedded image API."""

import argparse
import shutil
from pathlib import Path

from .analysis import apply_analysis
from .bootstrap import initialize_run
from .bundles import verify_archive
from .contracts import apply_contract, contract_template
from .documents import commit_documents, recover_documents
from .edits import apply_edits, edit_targets
from .export import export_bundle
from .images import (
    attach_image,
    budget_summary,
    import_legacy_failures,
    plan_image,
    prepare_cards,
    record_failure,
    register_image,
    set_reference,
)
from .layout import STYLES
from .rendering import render_cards
from .review import apply_review, write_review_page
from .source import validate_source
from .status import run_status
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
    command.add_argument("--resume", action="store_true")
    command.add_argument("--image-limit", type=int, default=10)
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
    command.add_argument("--resume", action="store_true")
    command = commands.add_parser("verify-export", help="check portable ZIP integrity without original book")
    command.add_argument("archive", type=Path)
    command = commands.add_parser("review", help="write a private offline human-review page")
    command.add_argument("--run", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--quotes-only", action="store_true")
    command = commands.add_parser("apply-review", help="validate and import actual human decisions")
    command.add_argument("record", type=Path)
    command.add_argument("--run", type=Path, required=True)
    command = commands.add_parser("edit-template", help="write a private version-bound edit request")
    command.add_argument("--run", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command = commands.add_parser("apply-edits", help="apply a validated edit batch without image calls")
    command.add_argument("request", type=Path)
    command.add_argument("--run", type=Path, required=True)
    command.add_argument("--allow-rewrite", action="store_true")
    command = commands.add_parser("recover", help="finish an interrupted JSON transaction")
    command.add_argument("--run", type=Path, required=True)
    command = commands.add_parser("contract-template", help="prepare explicit per-run intent; never infer count from cards")
    command.add_argument("--run", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command = commands.add_parser("apply-contract", help="version task scope without approving content or resetting calls")
    command.add_argument("request", type=Path)
    command.add_argument("--run", type=Path, required=True)
    command = commands.add_parser("status", help="show observed progress and stale/awaiting cards")
    command.add_argument("--run", type=Path, required=True)
    for name, help_text in [("image-plan", "reserve budget and save an application-tool request"),
                            ("image-fail", "record a failure or unknown tool outcome"),
                            ("image-import", "register an actual image file and its receipt"),
                            ("budget", "show conservative image call counts"),
                            ("import-call-log", "preserve prior failed/unknown call counts"),
                            ("prepare-cards", "prepare source-verified application-selected cards"),
                            ("attach-image", "attach a registered image to a card"),
                            ("set-reference", "set a registered character reference image")]:
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--run", type=Path, required=True)
        if name in {"image-plan", "import-call-log"}:
            command.add_argument("file", type=Path)
        elif name == "image-import":
            command.add_argument("source", type=Path)
            command.add_argument("--receipt", type=Path, required=True)
        elif name == "image-fail":
            command.add_argument("--call", required=True)
            command.add_argument("--status", choices=["failed", "unknown_outcome", "tool_unavailable"], required=True)
            command.add_argument("--failure-kind", required=True)
            command.add_argument("--note", required=True)
        elif name == "prepare-cards":
            command.add_argument("--count", type=int)
            command.add_argument("--style")
        elif name in {"attach-image", "set-reference"}:
            command.add_argument("asset_id")
            command.add_argument("--card" if name == "attach-image" else "--character", required=True)
    commands.add_parser("styles", help="print built-in, editable layout parameters")
    args = parser.parse_args()
    try:
        if args.command not in {"ingest", "styles", "recover", "status", "verify-export"}:
            recover_documents(args.run)
        if args.command == "ingest":
            report = initialize_run(args.source, args.run, title=args.title, encoding=args.encoding,
                                    size=args.chunk_size, overlap=args.overlap, resume=args.resume,
                                    image_limit=args.image_limit)
            print(f"Indexed {report['chapters_indexed']} chapters; {report['candidates']} candidates; reused={report['reused']}.")
        elif args.command == "styles":
            import json
            print(json.dumps(STYLES, ensure_ascii=False, indent=2))
        elif args.command == "render":
            report = render_cards(args.run, args.font, card_id=args.card, width=args.width,
                                  height=args.height, draft=not args.clean_candidate)
            print(f"Rendered {report['rendered']}; reused {report['reused']}; image calls 0.")
        elif args.command == "export":
            report = export_bundle(args.run, args.output, draft=args.draft,
                                   allow_rewrite=args.allow_rewrite, resume=args.resume)
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
        else:
            import json
            if args.command == "image-plan":
                report = plan_image(args.run, load_json(args.file))
            elif args.command == "image-fail":
                report = record_failure(args.run, args.call, args.status,
                                        failure_kind=args.failure_kind, note=args.note)
            elif args.command == "image-import":
                report = register_image(args.run, args.source, load_json(args.receipt))
            elif args.command == "budget":
                report = budget_summary(args.run)
            elif args.command == "import-call-log":
                report = import_legacy_failures(args.run, args.file)
            elif args.command == "prepare-cards":
                report = prepare_cards(args.run, count=args.count, style_id=args.style)
            elif args.command == "attach-image":
                report = attach_image(args.run, args.asset_id, args.card)
            elif args.command == "set-reference":
                report = set_reference(args.run, args.asset_id, args.character)
            elif args.command == "status":
                report = run_status(args.run)
            elif args.command == "verify-export":
                verified = verify_archive(args.archive)
                report = {k: v for k, v in verified.items() if k != "manifest"}
            elif args.command == "edit-template":
                if args.output.exists():
                    raise ValueError("edit template exists; choose a new version")
                report = edit_targets(args.run)
                save_json(args.output, report)
                report = {"output": str(args.output), "expected_fingerprint": report["expected_fingerprint"]}
            elif args.command == "apply-edits":
                report = apply_edits(args.run, load_json(args.request), allow_rewrite=args.allow_rewrite)
            elif args.command == "contract-template":
                if args.output.exists():
                    raise ValueError("contract template exists; choose a new version")
                report = contract_template(args.run)
                save_json(args.output, report)
                report = {"output": str(args.output), "expected_fingerprint": report["expected_fingerprint"]}
            elif args.command == "apply-contract":
                report = apply_contract(args.run, load_json(args.request))
            else:
                raise ValueError("unhandled command")
            print(json.dumps(report, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError) as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    main()
