"""Reviewed exports and visibly marked draft bundles; never include full book text."""

import os
import re
import uuid
from pathlib import Path

from PIL import Image

from .bundles import build_payload, verify_archive, write_archive
from .documents import recover_documents
from .images import validate_character_reference, validate_image_asset
from .quotes import check_display
from .rendering import validate_tracked_render
from .review_policy import VISUAL_POLICY_VERSION, visual_review_complete
from .source import validate_source
from .storage import contained, digest_file, fingerprint, load_json, now, save_json, sha256
from .workflow import Ledger


def review_fingerprint(card: dict, quote: dict, image_hash: str, layout_hash: str) -> str:
    import json
    material = {"visual_policy_version": VISUAL_POLICY_VERSION,
                "card": {k: v for k, v in card.items() if k != "review"},
                "quote": {k: v for k, v in quote.items() if k != "review"},
                "image_hash": image_hash, "layout_hash": layout_hash}
    return sha256(json.dumps(material, ensure_ascii=False, sort_keys=True).encode())


def quote_fingerprint(quote: dict, character: dict | None = None) -> str:
    import json
    material = {"quote": {k: v for k, v in quote.items() if k != "review"},
                "speaker_identity": {k: character.get(k) for k in ["character_id", "name", "aliases"]}
                if character else None}
    return sha256(json.dumps(material,
                             sort_keys=True, ensure_ascii=False).encode())


def _select(run: Path, *, draft: bool = False,
            allow_rewrite: bool = False) -> tuple:
    recover_documents(run)
    book = load_json(run / "book.json")
    validate_source(run, book)
    quotes = {q["quote_id"]: q for q in load_json(run / "quotes.json")}
    characters = load_json(run / "characters.json")
    cards = load_json(run / "cards.json")
    if not cards or len({c["card_id"] for c in cards}) != len(cards):
        raise ValueError("empty or duplicate card IDs")
    selected = []
    for card in cards:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", card["card_id"]):
            raise ValueError("card ID must be an ASCII identifier")
        quote = quotes[card["quote_id"]]
        check_display(book, quote, allow_rewrite)
        image = contained(run, card["poster"])
        layout_path = image.with_suffix(".layout.json")
        layout = load_json(layout_path)
        background = contained(run, card["background"])
        if digest_file(background) != layout["background_sha256"]:
            raise ValueError("background changed; recompose and re-review")
        with Image.open(image) as picture:
            if picture.format != "PNG" or picture.size != (1080, 1440) or not picture.info.get("icc_profile"):
                raise ValueError("export requires 1080x1440 PNG with sRGB profile")
        image_hash = digest_file(image)
        if (layout["output_sha256"] != image_hash or layout["text"] != quote["display_text"]
                or layout["rewrite"] != (quote["text_mode"] == "rewrite")
                or layout["character"] != characters[card["character_id"]]["name"]
                or layout["title"] != book["title"]
                or layout["style_id"] != card["style_id"]):
            raise ValueError("stale or mismatched poster/layout")
        if draft != layout["draft"]:
            raise ValueError("draft flag must match visible poster label")
        validate_tracked_render(run, card, quote, book, characters[card["character_id"]],
                                layout, required=not draft, source_checked=True)
        fingerprint = review_fingerprint(card, quote, image_hash, digest_file(layout_path))
        if not draft:
            review = card.get("review", {})
            speaker_review = quote.get("review", {})
            image_asset = validate_image_asset(run, card, required=True)
            if image_asset["source_kind"] not in {"generated", "user_provided"}:
                raise ValueError("test fixtures cannot be final exported imagery")
            if (quote["quote_type"] not in {"dialogue", "monologue"}
                    or quote["speaker"]["status"] in {"ambiguous", "narration"}
                    or quote["speaker"]["character_id"] != card["character_id"]):
                raise ValueError("named final card has ambiguous or different speaker")
            if (speaker_review.get("status") != "approved"
                    or speaker_review.get("reviewer_type") != "human"
                    or not speaker_review.get("reviewer")
                    or speaker_review.get("fingerprint") != quote_fingerprint(quote, characters[card["character_id"]])):
                raise ValueError("speaker has not received human approval")
            if (review.get("status") != "approved" or review.get("reviewer_type") != "human"
                    or review.get("fingerprint") != fingerprint or not review.get("reviewer")):
                raise ValueError("human visual approval missing or invalidated")
            if not visual_review_complete(review):
                raise ValueError("human visual checklist missing or incomplete")
            if image_asset["source_kind"] == "generated":
                validate_character_reference(run, characters[card["character_id"]], required=True)
        selected.append((card, quote, image, layout_path, fingerprint))
    return book, characters, selected


def _snapshot(book: dict, characters: dict, selected: list, draft: bool, allow_rewrite: bool) -> dict:
    return {"schema_version": 2, "source": {k: book[k] for k in ["source_hash", "canonical_hash", "title"]},
            "draft": draft, "allow_rewrite": allow_rewrite,
            "characters": {c["character_id"]: characters[c["character_id"]] for c, *_ in selected},
            "cards": [{"card": card, "quote": quote, "poster_sha256": digest_file(image),
                       "layout_sha256": digest_file(layout), "approval": approval}
                      for card, quote, image, layout, approval in selected],
            "exporter_code": digest_file(Path(__file__)),
            "bundler_code": digest_file(Path(build_payload.__code__.co_filename))}


def _record_export(run: Path, destination: Path, report: dict, snapshot: dict):
    identity = fingerprint({"destination": str(destination), "input": report["input_fingerprint"],
                            "zip": report["zip_sha256"]})
    path = run / ".state" / "exports" / (identity + ".json")
    with Ledger(run / ".state/ledger.sqlite") as ledger, ledger.db:
        ledger.db.execute("UPDATE config SET value=value WHERE key='limit'")
        if not path.exists():
            save_json(path, {"created_at": now(), "destination": str(destination),
                             "report": report, "snapshot": snapshot})
        else:
            previous = load_json(path)
            if previous["destination"] != str(destination) or previous["report"] != report or previous["snapshot"] != snapshot:
                raise ValueError("export history record damaged")
        key = "export_event:" + identity
        if not ledger.db.execute("SELECT 1 FROM config WHERE key=?", (key,)).fetchone():
            ledger._event("export_published", {"record_id": identity,
                                              "zip_sha256": report["zip_sha256"],
                                              "input_fingerprint": report["input_fingerprint"]})
            ledger.db.execute("INSERT INTO config VALUES (?,?)", (key, "recorded"))


def _ready_report(folder: Path, zip_name: str, signature: str) -> dict | None:
    archive = folder / zip_name
    if not archive.is_file():
        return None
    if {p.name for p in folder.iterdir()} - {zip_name, "export-report.json"}:
        return None
    try:
        verified = verify_archive(archive)
        manifest = verified["manifest"]
        if manifest["input_fingerprint"] != signature:
            return None
        report = {**manifest, "zip_sha256": digest_file(archive), "zip_integrity": "passed"}
        report_path = folder / "export-report.json"
        if report_path.exists() and load_json(report_path) != report:
            return None
        if not report_path.exists():
            save_json(report_path, report)
        if {p.name for p in folder.iterdir()} != {zip_name, "export-report.json"}:
            return None
        return report
    except (ValueError, OSError):
        return None


def export_bundle(run: Path, destination: Path, *, draft: bool = False,
                  allow_rewrite: bool = False, resume: bool = False) -> dict:
    run, destination = run.resolve(), destination.resolve()
    book, characters, selected = _select(run, draft=draft, allow_rewrite=allow_rewrite)
    snapshot = _snapshot(book, characters, selected, draft, allow_rewrite)
    signature = fingerprint(snapshot)
    zip_name = "draft-review.zip" if draft else "export.zip"
    if destination == destination.parent:
        raise ValueError("a named export directory is required")
    staging_root = (destination.parent / ".export-staging").resolve()
    if destination == staging_root:
        raise ValueError("export destination cannot be the staging root")
    staging_root.mkdir(parents=True, exist_ok=True)
    stage = (staging_root / f"{destination.name}-{signature[:20]}").resolve()
    if stage.parent != staging_root or destination.parent != staging_root.parent:
        raise ValueError("export paths escape requested parent")
    with Ledger(staging_root / "coordination.sqlite") as lock, lock.db:
        lock.db.execute("UPDATE config SET value=value WHERE key='limit'")
        if destination.exists():
            if not resume:
                raise ValueError("export destination already exists; use --resume or a new version")
            report = _ready_report(destination, zip_name, signature)
            if not report:
                raise ValueError("existing export damaged or inputs changed; preserve it and choose a new version")
            _record_export(run, destination, report, snapshot)
            return {**report, "reused": True}
        if stage.exists() and not resume:
            raise ValueError("unfinished export checkpoint exists; use --resume")
        stage.mkdir(exist_ok=True)
        input_file = stage / "input-snapshot.json"
        if input_file.exists() and load_json(input_file) != snapshot:
            raise ValueError("export checkpoint inputs differ")
        if not input_file.exists():
            save_json(input_file, snapshot)
        attempts = stage / "attempts"
        attempts.mkdir(exist_ok=True)
        ready, report = None, None
        try:
            if resume:
                for attempt in sorted(attempts.iterdir()):
                    if not attempt.is_dir():
                        continue
                    candidate = attempt / "release"
                    report = _ready_report(candidate, zip_name, signature)
                    if report:
                        ready = candidate
                        break
            reused = ready is not None
            if ready is None:
                attempt = attempts / uuid.uuid4().hex
                attempt.mkdir()
                ready = attempt / "release"
                ready.mkdir()
                payload = attempt / "payload"
                build_payload(run, payload, book, characters, selected, draft=draft, signature=signature)
                write_archive(payload, ready / zip_name)
                report = _ready_report(ready, zip_name, signature)
                if not report:
                    raise ValueError("new export failed archive verification")
            current = _snapshot(*_select(run, draft=draft, allow_rewrite=allow_rewrite), draft, allow_rewrite)
            if fingerprint(current) != signature:
                raise ValueError("inputs changed while exporting; no final package was published")
            # Both absolute paths are checked before this Windows directory move.
            if not ready.resolve().is_relative_to(stage) or destination.resolve().parent != staging_root.parent:
                raise ValueError("unsafe export publication path")
            if destination.exists():
                raise ValueError("export destination appeared; nothing was overwritten")
            os.rename(ready, destination)
            _record_export(run, destination, report, snapshot)
            return {**report, "reused": reused}
        except Exception as error:
            save_json(stage / f"failure-{uuid.uuid4().hex}.json", {"type": type(error).__name__, "note": str(error)})
            raise


def export_status(run: Path) -> dict:
    records = run / ".state" / "exports"
    if not records.is_dir() or not list(records.glob("*.json")):
        return {"state": "not_exported"}
    row = max((load_json(p) for p in records.glob("*.json")), key=lambda r: r["created_at"])
    report = row["report"]
    archive = Path(row["destination"]) / ("draft-review.zip" if report["draft"] else "export.zip")
    try:
        if not archive.exists():
            return {"state": "missing", "input_fingerprint": report["input_fingerprint"]}
        if digest_file(archive) != report["zip_sha256"]:
            return {"state": "corrupt", "input_fingerprint": report["input_fingerprint"]}
        snapshot = _snapshot(*_select(run, draft=report["draft"], allow_rewrite=row["snapshot"]["allow_rewrite"]),
                             report["draft"], row["snapshot"]["allow_rewrite"])
        return {"state": "current" if fingerprint(snapshot) == report["input_fingerprint"] else "invalidated",
                "draft": report["draft"], "input_fingerprint": report["input_fingerprint"]}
    except (ValueError, OSError, KeyError) as error:
        return {"state": "invalidated", "reason": str(error)}
