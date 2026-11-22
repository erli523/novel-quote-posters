"""Reviewed exports and visibly marked draft bundles; never include full book text."""

import csv
import io
import tempfile
import zipfile
from pathlib import Path

from PIL import Image

from .quotes import check_display
from .storage import contained, digest_file, load_json, save_json, sha256


def review_fingerprint(card: dict, quote: dict, image_hash: str, layout_hash: str) -> str:
    import json
    material = {"card": {k: v for k, v in card.items() if k != "review"},
                "quote": {k: v for k, v in quote.items() if k != "review"},
                "image_hash": image_hash, "layout_hash": layout_hash}
    return sha256(json.dumps(material, ensure_ascii=False, sort_keys=True).encode())


def quote_fingerprint(quote: dict) -> str:
    import json
    return sha256(json.dumps({k: v for k, v in quote.items() if k != "review"},
                             sort_keys=True, ensure_ascii=False).encode())


def export_bundle(run: Path, destination: Path, *, draft: bool = False,
                  allow_rewrite: bool = False) -> dict:
    book = load_json(run / "book.json")
    source_path = contained(run, "source." + book["format"])
    if digest_file(source_path) != book["source_hash"]:
        raise ValueError("source file hash changed")
    quotes = {q["quote_id"]: q for q in load_json(run / "quotes.json")}
    characters = load_json(run / "characters.json")
    cards = load_json(run / "cards.json")
    if not cards or len({c["card_id"] for c in cards}) != len(cards):
        raise ValueError("empty or duplicate card IDs")
    selected, assets = [], []
    for card in cards:
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
        fingerprint = review_fingerprint(card, quote, image_hash, digest_file(layout_path))
        if not draft:
            review = card.get("review", {})
            speaker_review = quote.get("review", {})
            if card.get("image_kind") != "generated":
                raise ValueError("final export requires real generated imagery")
            if (quote["speaker"]["status"] in {"ambiguous", "narration"}
                    or quote["speaker"]["character_id"] != card["character_id"]):
                raise ValueError("named final card has ambiguous or different speaker")
            if (speaker_review.get("status") != "approved"
                    or speaker_review.get("reviewer_type") != "human"
                    or not speaker_review.get("reviewer")
                    or speaker_review.get("fingerprint") != quote_fingerprint(quote)):
                raise ValueError("speaker has not received human approval")
            if (review.get("status") != "approved" or review.get("reviewer_type") != "human"
                    or review.get("fingerprint") != fingerprint or not review.get("reviewer")):
                raise ValueError("human visual approval missing or invalidated")
        selected.append((card, quote, image, layout_path, fingerprint))
    if destination.exists():
        raise ValueError("export destination already exists; choose a new version")
    destination.mkdir(parents=True)
    zip_path = destination / ("draft-review.zip" if draft else "export.zip")
    try:
        with tempfile.TemporaryDirectory(dir=destination) as temporary:
            stage = Path(temporary)
            (stage / "douyin_batch").mkdir()
            csv_file = io.StringIO(newline="")
            fields = ["sequence", "character", "quote_exact", "display_text", "text_mode",
                      "chapter", "start", "end", "source_hash", "review_status"]
            writer = csv.DictWriter(csv_file, fieldnames=fields)
            writer.writeheader()
            for index, (card, quote, image, layout_path, fingerprint) in enumerate(selected, 1):
                name = f"{index:03d}_{card['card_id']}.png"
                if any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in card["card_id"]):
                    raise ValueError("card ID must be an ASCII identifier")
                target = stage / "douyin_batch" / name
                target.write_bytes(image.read_bytes())
                preview = image.with_name(image.stem + "-preview.png")
                if preview.is_file():
                    (stage / "previews").mkdir(exist_ok=True)
                    (stage / "previews" / name).write_bytes(preview.read_bytes())
                row = {"sequence": index, "character": characters[card["character_id"]]["name"],
                       "quote_exact": quote["quote_exact"], "display_text": quote["display_text"],
                       "text_mode": quote["text_mode"], "chapter": quote["source"]["chapter"],
                       "start": quote["source"]["start"], "end": quote["source"]["end"],
                       "source_hash": quote["source"]["source_hash"],
                       "review_status": "pending" if draft else "approved"}
                # Spreadsheet formula injection protection; JSON preserves exact original strings.
                writer.writerow({k: "'"+v if isinstance(v, str) and v[:1] in "=+-@" and v else v
                                 for k, v in row.items()})
                assets.append({"path": f"douyin_batch/{name}", "sha256": digest_file(target),
                               "card_id": card["card_id"], "quote_id": quote["quote_id"],
                               "fingerprint": fingerprint, "image_kind": card.get("image_kind", "unknown")})
            (stage / "quotes.csv").write_text(csv_file.getvalue(), encoding="utf-8-sig", newline="")
            save_json(stage / "cards.json", cards)
            save_json(stage / "quotes.json", [q for _, q, *_ in selected])
            save_json(stage / "characters.json", characters)
            manifest = {"schema_version": 1, "draft": draft, "source_hash": book["source_hash"],
                        "book_title": book["title"], "assets": assets,
                        "review_status": "pending" if draft else "approved"}
            save_json(stage / "manifest.json", manifest)
            (stage / "发布说明.txt").write_text(
                ("待审核演示包，不是已批准的发布成品。\n" if draft else "已审阅图集，请手工上传。\n")
                + "按 douyin_batch 文件名前缀排序；出处见 quotes.csv。\n未自动发布抖音。\n",
                encoding="utf-8")
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(stage.rglob("*")):
                    if path.is_file():
                        archive.write(path, path.relative_to(stage).as_posix())
        with zipfile.ZipFile(zip_path) as archive:
            if archive.testzip():
                raise ValueError("ZIP integrity failure")
            for asset in assets:
                if sha256(archive.read(asset["path"])) != asset["sha256"]:
                    raise ValueError("ZIP content hash mismatch")
        save_json(destination / "export-report.json", {**manifest, "zip_sha256": digest_file(zip_path),
                                                      "zip_integrity": "passed"})
        return manifest
    except Exception:
        # A failed output is explicitly marked; never leave an apparently valid export.
        zip_path.unlink(missing_ok=True)
        save_json(destination / "failed.json", {"status": "failed"})
        raise
