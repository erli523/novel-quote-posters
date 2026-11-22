"""Report observed progress, freshness and review state without inventing completion."""

from pathlib import Path

from .documents import recover_documents
from .export import quote_fingerprint, review_fingerprint
from .images import budget_summary, validate_image_asset
from .rendering import validate_tracked_render
from .source import validate_source
from .storage import contained, digest_file, load_json


def run_status(run: Path) -> dict:
    if not run.is_dir():
        staging = run.resolve().parent / ".ingest-staging"
        checkpoints = ([p for p in staging.iterdir() if p.is_dir()
                        and p.name.startswith(run.name + "-")] if staging.is_dir() else [])
        return {"ingest": "checkpoint_found" if checkpoints else "not_started",
                "published_run": False, "live_process_claim": False}
    recover_documents(run)
    book = load_json(run / "book.json")
    try:
        validate_source(run, book)
    except (ValueError, OSError, KeyError) as error:
        return {"ingest": "invalid", "published_run": True, "reason": str(error),
                "overall_acceptance": "not_passed"}
    windows = load_json(run / "chunks.json")
    covered = 0
    gap = False
    for window in sorted(windows, key=lambda w: w["start"]):
        gap = gap or window["start"] > covered
        covered = max(covered, window["end"])
    quotes = {q["quote_id"]: q for q in load_json(run / "quotes.json")}
    characters = load_json(run / "characters.json") if (run / "characters.json").exists() else {}
    cards = load_json(run / "cards.json") if (run / "cards.json").exists() else []
    rows = []
    for card in cards:
        row = {"card_id": card["card_id"], "state": "awaiting_image"}
        try:
            if not card.get("background"):
                rows.append(row)
                continue
            asset = validate_image_asset(run, card)
            if not card.get("poster") or not contained(run, card["poster"]).is_file():
                row["state"] = "awaiting_render"
            else:
                image = contained(run, card["poster"])
                report_path = image.with_suffix(".layout.json")
                report = load_json(report_path)
                if digest_file(image) != report["output_sha256"]:
                    raise ValueError("composed image damaged")
                quote = quotes[card["quote_id"]]
                validate_tracked_render(run, card, quote, book, characters[card["character_id"]],
                                        report, required=True, source_checked=True)
                current = review_fingerprint(card, quote, digest_file(image), digest_file(report_path))
                visual = card.get("review", {})
                semantic = quote.get("review", {})
                approved = (visual.get("status") == "approved" and visual.get("reviewer_type") == "human"
                            and visual.get("fingerprint") == current and bool(visual.get("reviewer"))
                            and semantic.get("status") == "approved" and semantic.get("reviewer_type") == "human"
                            and semantic.get("fingerprint") == quote_fingerprint(quote)
                            and quote["speaker"]["character_id"] == card["character_id"]
                            and quote["speaker"]["status"] in {"explicit", "context_inferred"})
                row["state"] = ("draft_ready" if report["draft"] else "approved_candidate"
                                if approved and asset and asset["source_kind"] != "test_fixture"
                                else "awaiting_review")
            row["provenance"] = asset["source_kind"] if asset else "unregistered"
        except (ValueError, OSError, KeyError) as error:
            row.update(state="invalidated", reason=str(error))
        rows.append(row)
    counts = {name: sum(r["state"] == name for r in rows) for name in sorted({r["state"] for r in rows})}
    return {"ingest": "indexed", "published_run": True, "source_hash": book["source_hash"],
            "chapters_indexed": len(book["chapters"]), "chunks_total": len(windows),
            "chunks_application_processed": sum(w.get("semantic_status") == "application_processed" for w in windows),
            "index_ranges_complete": not gap and covered == len(book["text"]),
            "semantic_quality_claim": "requires_actual_review", "candidate_count": len(quotes),
            "card_counts": counts, "cards": rows, "budget": budget_summary(run),
            "overall_acceptance": "not_inferred_from_progress"}
