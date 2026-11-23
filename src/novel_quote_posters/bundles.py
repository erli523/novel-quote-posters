"""Portable export payloads and standalone integrity checks; no book or provider calls."""

import csv
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from PIL import Image

from .albums import album_review_complete, album_target, portable_album_review, selection_target
from .images import validate_image_asset
from .storage import contained, copy_atomic, digest_file, load_json, save_json, sha256


def _fields(value: dict, names: list[str]) -> dict:
    return {k: value[k] for k in names if k in value}


def _evidence(rows: list) -> list:
    return [_fields(row, ["start", "end", "text"]) for row in rows]


def portable_quote(quote: dict) -> dict:
    value = _fields(quote, ["quote_id", "quote_exact", "display_text", "text_mode", "source", "quote_type"])
    value["speaker"] = _fields(quote["speaker"], ["status", "character_id", "explanation"])
    value["speaker"]["evidence"] = _evidence(quote["speaker"].get("evidence", []))
    value["review"] = _fields(quote.get("review", {}), ["status", "reviewer_type", "reviewer", "reviewed_at", "note", "fingerprint"])
    return value


def portable_character(run: Path, character: dict) -> dict:
    result = _fields(character, ["character_id", "name", "aliases", "visual_anchor_version", "reference_asset_id"])
    result["traits"] = [{**_fields(t, ["kind", "value"]), "evidence": _evidence(t.get("evidence", []))}
                        for t in character.get("traits", [])]
    if character.get("reference_image"):
        result["reference_image"] = {"sha256": digest_file(contained(run, character["reference_image"])),
                                      "available_in": "original_run", "included": False}
    return result


def build_payload(run: Path, folder: Path, book: dict, characters: dict, selected: list,
                  *, draft: bool, signature: str) -> dict:
    folder.mkdir(parents=True, exist_ok=False)
    for name in ["douyin_batch", "previews", "layouts"]:
        (folder / name).mkdir()
    assets, cards, quotes, provenance = [], [], {}, {}
    csv_file = io.StringIO(newline="")
    fields = ["sequence", "character", "quote_exact", "display_text", "text_mode", "chapter",
              "start", "end", "source_hash", "review_status"]
    writer = csv.DictWriter(csv_file, fieldnames=fields)
    writer.writeheader()
    for index, (card, quote, image, report_path, approval_hash) in enumerate(selected, 1):
        filename = f"{index:03d}_{card['card_id']}"
        poster = folder / "douyin_batch" / (filename + ".png")
        copy_atomic(image, poster)
        with Image.open(image) as picture:
            picture.resize((360, 480), Image.Resampling.LANCZOS).save(
                folder / "previews" / (filename + ".png"), icc_profile=picture.info["icc_profile"])
        original = load_json(report_path)
        report = _fields(original, ["schema_version", "style_id", "width", "height", "font_sha256",
            "background_sha256", "output_sha256", "text", "character", "title", "draft", "rewrite",
            "font_size", "text_box", "visual_review", "input_fingerprint"])
        report["lines"] = [_fields(line, ["text", "xy", "bbox"]) for line in original["lines"]]
        report["style"] = _fields(original["style"], ["bg", "fg", "accent", "image", "panel", "text"])
        # Physical font paths are runtime locators; the font hash and layout survive export.
        report["render_options"] = _fields(original.get("render_options", {}), ["width", "height", "draft"])
        report["preview"] = f"previews/{filename}.png"
        report["preview_sha256"] = digest_file(folder / report["preview"])
        report["source_report_sha256"] = digest_file(report_path)
        save_json(folder / "layouts" / (filename + ".json"), report)
        portable = _fields(card, ["card_id", "quote_id", "character_id", "style_id", "image_kind", "image_asset_id", "layout_overrides"])
        if "layout_overrides" in portable:
            portable["layout_overrides"] = _fields(portable["layout_overrides"], ["bg", "fg", "accent", "image", "panel", "text"])
        portable.update(poster=f"douyin_batch/{filename}.png", preview=f"previews/{filename}.png",
                        layout_report=f"layouts/{filename}.json", source_review_fingerprint=approval_hash,
                        background={"sha256": report["background_sha256"], "included": False,
                                    "available_in": "original_run"})
        portable["review"] = _fields(card.get("review", {}), ["status", "reviewer_type", "reviewer", "reviewed_at", "note", "fingerprint", "visual_policy_version", "visual_checks", "view_hashes", "crop_box"])
        cards.append(portable)
        quotes[quote["quote_id"]] = portable_quote(quote)
        asset = validate_image_asset(run, card)
        if asset:
            provenance[asset["asset_id"]] = _fields(asset, ["asset_id", "sha256", "source_kind", "character_id", "width", "height", "format", "call_id", "input_hash", "request_kind", "tool", "model_version", "seed", "cost", "evidence_type", "receipt_sha256"])
            provenance[asset["asset_id"]]["raw_image_included"] = False
        row = {"sequence": index, "character": characters[card["character_id"]]["name"],
               "quote_exact": quote["quote_exact"], "display_text": quote["display_text"],
               "text_mode": quote["text_mode"], "chapter": quote["source"]["chapter"],
               "start": quote["source"]["start"], "end": quote["source"]["end"],
               "source_hash": quote["source"]["source_hash"], "review_status": "pending" if draft else "approved"}
        writer.writerow({k: "'"+v if isinstance(v, str) and v and v[:1] in "=+-@" else v for k, v in row.items()})
        assets.append({"path": portable["poster"], "sha256": digest_file(poster), "card_id": card["card_id"],
                       "quote_id": quote["quote_id"], "fingerprint": approval_hash,
                       "image_kind": card.get("image_kind", "unknown"), "image_asset_id": card.get("image_asset_id")})
    (folder / "quotes.csv").write_text(csv_file.getvalue(), encoding="utf-8-sig", newline="")
    save_json(folder / "cards.json", cards)
    save_json(folder / "quotes.json", list(quotes.values()))
    used = {c["character_id"] for c in cards}
    save_json(folder / "characters.json", {key: portable_character(run, characters[key]) for key in sorted(used)})
    save_json(folder / "image-provenance.json", provenance)
    save_json(folder / "album-review.json", portable_album_review(run, selection_target(book, selected, draft), draft))
    (folder / "发布说明.txt").write_text(
        ("待审核演示包，不是已批准的发布成品。\n" if draft else "已审阅图集，请手工上传。\n")
        + "按 douyin_batch 前缀排序，出处见 quotes.csv，布局见 layouts。未自动发布抖音。\n"
        + "原文、字体、无字底图、原始提示词留在原任务中；本包是成品与溯源摘要，不是完整重建环境。\n"
        + "审核和来源记录为声明；原句、归属和工具真实性仍需对照原任务。\n", encoding="utf-8")
    inventory = [{"path": p.relative_to(folder).as_posix(), "sha256": digest_file(p), "size": p.stat().st_size}
                 for p in sorted(folder.rglob("*")) if p.is_file()]
    manifest = {"schema_version": 3, "draft": draft, "source_hash": book["source_hash"],
                "book_title": book["title"], "input_fingerprint": signature,
                "assets": assets, "files": inventory, "review_status": "pending" if draft else "approved",
                "verification_scope": "integrity_only_without_original_run"}
    save_json(folder / "manifest.json", manifest)
    return manifest


def write_archive(folder: Path, output: Path):
    """Fixed ZIP metadata makes unchanged payloads produce identical archive bytes."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w+b", prefix=".building-", suffix=".zip",
                                     dir=output.parent, delete=False) as stream:
        temporary = Path(stream.name)
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(folder.rglob("*")):
                if path.is_file():
                    info = zipfile.ZipInfo(path.relative_to(folder).as_posix(), (1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.create_system = 3
                    info.external_attr = 0o100644 << 16
                    archive.writestr(info, path.read_bytes())
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, output)


def verify_archive(path: Path) -> dict:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            names = [e.filename for e in entries]
            if len(names) != len(set(names)) or len(names) > 10000 or sum(e.file_size for e in entries) > 2*1024**3:
                raise ValueError("archive duplicate/member/expanded-size limit failed")
            for entry in entries:
                relative = PurePosixPath(entry.filename)
                if (relative.is_absolute() or ".." in relative.parts or "\\" in entry.filename
                        or ":" in entry.filename or relative.as_posix() != entry.filename or entry.is_dir()
                        or (entry.external_attr >> 16) & 0o170000 == 0o120000):
                    raise ValueError("unsafe archive member")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("schema_version") not in {2, 3}:
                raise ValueError("unsupported export manifest version")
            inventory = manifest["files"]
            if len({row["path"] for row in inventory}) != len(inventory):
                raise ValueError("duplicate payload inventory")
            if set(names) != {row["path"] for row in inventory} | {"manifest.json"}:
                raise ValueError("archive contains unlisted or missing files")
            for row in inventory:
                raw = archive.read(row["path"])
                if len(raw) != row["size"] or sha256(raw) != row["sha256"]:
                    raise ValueError("archive payload checksum mismatch: " + row["path"])
            cards = json.loads(archive.read("cards.json"))
            quotes = {q["quote_id"]: q for q in json.loads(archive.read("quotes.json"))}
            characters = json.loads(archive.read("characters.json"))
            if len(cards) != len(manifest["assets"]) or len({c["card_id"] for c in cards}) != len(cards):
                raise ValueError("archive card counts/IDs differ")
            for card, asset in zip(cards, manifest["assets"]):
                if (card["card_id"] != asset["card_id"] or card["poster"] != asset["path"]
                        or card["quote_id"] != asset["quote_id"] or card["character_id"] not in characters):
                    raise ValueError("archive card cross-reference mismatch")
                layout = json.loads(archive.read(card["layout_report"]))
                quote = quotes[card["quote_id"]]
                if layout["text"] != quote["display_text"] or layout["output_sha256"] != asset["sha256"] or layout["draft"] != manifest["draft"]:
                    raise ValueError("archive text/layout mismatch")
                for member, expected in [(card["poster"], (1080, 1440)), (card["preview"], (360, 480))]:
                    raw = archive.read(member)
                    with Image.open(io.BytesIO(raw)) as picture:
                        if picture.format != "PNG" or picture.size != expected or not picture.info.get("icc_profile"):
                            raise ValueError("invalid archived PNG dimensions/profile")
                        picture.verify()
                if sha256(archive.read(card["poster"])) != asset["sha256"] or sha256(archive.read(card["preview"])) != layout["preview_sha256"]:
                    raise ValueError("archive image/layout checksum mismatch")
            if manifest["schema_version"] == 3:
                target = album_target({"source_hash": manifest["source_hash"], "title": manifest["book_title"]}, [
                    {"card_id": a["card_id"], "quote_id": a["quote_id"], "fingerprint": a["fingerprint"],
                     "image_sha256": a["sha256"], "draft": manifest["draft"]} for a in manifest["assets"]])
                review = json.loads(archive.read("album-review.json"))
                if (not target or not isinstance(review, dict) or review.get("fingerprint") != target["fingerprint"]
                        or review.get("material") != target["material"]
                        or (not manifest["draft"] and not album_review_complete(review, target))
                        or (manifest["draft"] and review.get("status") != "pending")):
                    raise ValueError("archive whole-album review/order mismatch")
            return {"manifest": manifest, "files_checked": len(inventory), "integrity": "passed",
                    "authenticity": "not_authenticated", "original_quote_verification": "requires_original_run"}
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError, OSError) as error:
        raise ValueError("invalid or incomplete export archive: " + str(error)) from error
