"""Recoverable local six-style composition from explicit, source-bound application plans."""

import copy
import importlib.metadata
import os
import re
import uuid
from pathlib import Path

from fontTools.ttLib import TTLibError
from PIL import Image, ImageDraw, ImageFont

from . import layout, layout_options
from .contracts import allocation_summary, load_contract, selection_issues
from .documents import recover_documents
from .images import validate_character_reference, validate_image_asset
from .quotes import check_display
from .review import STYLE_LABELS
from .source import validate_source
from .storage import contained, copy_atomic, digest_file, fingerprint, load_json, now, save_json
from .workflow import Ledger

ROW_KEYS = {"style_id", "asset_id", "font", "font_license_note", "reuse_reason", "layout_overrides"}
PLAN_KEYS = {
    "schema_version",
    "source_hash",
    "expected_content_fingerprint",
    "quote_id",
    "character_id",
    "draft",
    "editor",
    "reason",
    "styles",
}


def _source(run: Path, quote_id: str):
    book = load_json(run / "book.json")
    validate_source(run, book)
    quotes = {q["quote_id"]: q for q in load_json(run / "quotes.json")}
    quote = quotes[quote_id]
    character_id = quote["speaker"]["character_id"]
    character = load_json(run / "characters.json")[character_id]
    contract = load_contract(run, book)
    if contract is None:
        raise ValueError("sample rendering requires an explicit task contract")
    check_display(book, quote, allow_rewrite=contract["definition"]["allow_rewrite"])
    if quote["speaker"]["status"] not in {"explicit", "context_inferred"} or quote[
        "quote_type"
    ] not in {"dialogue", "monologue"}:
        raise ValueError("named samples require an attributed dialogue or monologue")
    material = {
        "book": {k: book[k] for k in ["source_hash", "canonical_hash", "title", "parser_version"]},
        "quote": {k: v for k, v in quote.items() if k != "review"},
        "character": character,
        "task_contract_fingerprint": contract["fingerprint"],
    }
    return book, quote, character, contract, material


def sample_template(run: Path, quote_id: str, font: Path) -> dict:
    recover_documents(run)
    book, quote, character, _contract, material = _source(run, quote_id)
    assets = load_json(run / "image-assets.json")
    return {
        "schema_version": 1,
        "source_hash": book["source_hash"],
        "expected_content_fingerprint": fingerprint(material),
        "quote_id": quote_id,
        "character_id": character["character_id"],
        "draft": True,
        "editor": "",
        "reason": "",
        "styles": [
            {
                "style_id": s,
                "asset_id": "",
                "font": str(font.resolve()),
                "font_license_note": "",
                "reuse_reason": "",
                "layout_overrides": {},
            }
            for s in STYLE_LABELS
        ],
        "available": {
            "assets": [
                {
                    "asset_id": a["asset_id"],
                    "width": a["width"],
                    "height": a["height"],
                    "source_kind": a["source_kind"],
                }
                for a in assets.values()
                if a["character_id"] == character["character_id"]
            ],
            "quote_exact": quote["quote_exact"],
            "note": "Choose each registered background and explain visual compatibility; not a semantic approval.",
        },
    }


def _normalize(request: dict) -> dict:
    if (
        not isinstance(request, dict)
        or set(request) - {"available"} != PLAN_KEYS
        or type(request.get("schema_version")) is not int
        or request["schema_version"] != 1
    ):
        raise ValueError("sample plan schema/fields invalid")
    plan = copy.deepcopy({k: v for k, v in request.items() if k != "available"})
    if type(plan["draft"]) is not bool or any(
        not isinstance(plan[k], str) or not plan[k].strip()
        for k in [
            "source_hash",
            "expected_content_fingerprint",
            "quote_id",
            "character_id",
            "editor",
            "reason",
        ]
    ):
        raise ValueError("sample plan requires source, content version, named editor and reason")
    rows = plan["styles"]
    if (
        not isinstance(rows, list)
        or len(rows) != 6
        or any(
            not isinstance(r, dict) or set(r) != ROW_KEYS or not isinstance(r["style_id"], str)
            for r in rows
        )
        or {r["style_id"] for r in rows} != set(STYLE_LABELS)
    ):
        raise ValueError("sample plan requires six complete unique style rows")
    for row in rows:
        if any(
            not isinstance(row[k], str) or not row[k].strip()
            for k in ["asset_id", "font", "font_license_note", "reuse_reason"]
        ):
            raise ValueError(
                "every sample needs an asset, font, local font use note and compatibility reason"
            )
        if not isinstance(row["layout_overrides"], dict):
            raise ValueError("sample layout overrides must be an object")  # noqa: TRY004 - CLI data error
        row["layout_overrides"] = layout_options.normalize_layout_overrides(row["layout_overrides"])
        row["font"] = str(Path(row["font"]).resolve())
    plan["styles"] = sorted(rows, key=lambda row: list(STYLE_LABELS).index(row["style_id"]))
    return plan


def _inputs(run: Path, request: dict, ledger: Ledger | None = None) -> dict:
    plan = _normalize(request)
    book, quote, character, contract, material = _source(run, plan["quote_id"])
    if (
        plan["source_hash"] != book["source_hash"]
        or plan["character_id"] != character["character_id"]
        or plan["expected_content_fingerprint"] != fingerprint(material)
    ):
        raise ValueError("sample plan content changed; prepare a fresh template")
    reference = validate_character_reference(run, character, required=True, ledger=ledger)
    assets = load_json(run / "image-assets.json")
    code = {
        "samples": digest_file(Path(__file__)),
        "layout": digest_file(Path(layout.__file__)),
        "layout_options": digest_file(Path(layout_options.__file__)),
    }
    packages = {p: importlib.metadata.version(p) for p in ["Pillow", "fonttools"]}
    rows = {}
    for row in plan["styles"]:
        if row["asset_id"] not in assets:
            raise ValueError("unknown sample image asset")
        asset = assets[row["asset_id"]]
        card = {
            "card_id": row["style_id"],
            "quote_id": quote["quote_id"],
            "character_id": character["character_id"],
            "style_id": row["style_id"],
            "background": asset["path"],
            "image_kind": asset["source_kind"],
            "image_asset_id": row["asset_id"],
        }
        validate_image_asset(run, card, required=True, ledger=ledger)
        if asset["source_kind"] not in {"generated", "user_provided"}:
            raise ValueError("managed samples require registered non-test imagery")
        issues = selection_issues(contract, [card], {quote["quote_id"]: quote}, check_count=False)
        if issues:
            raise ValueError("sample selection differs from task contract: " + "; ".join(issues))
        font = Path(row["font"])
        if not font.is_file():
            raise ValueError(
                "sample font missing; preserve prior versions and provide a valid font"
            )
        # Check the declared geometry and glyphs for all six before producing any output.
        layout.resolve_style(row["style_id"], quote["display_text"], row["layout_overrides"])
        try:
            layout.glyphs(
                font,
                quote["display_text"]
                + character["name"]
                + book["title"]
                + quote["source"]["chapter"]
                + "待审核用户改写"
                + "".join(STYLE_LABELS.values()),
            )
        except TTLibError as error:
            raise ValueError("sample font is not a readable supported font") from error
        rows[row["style_id"]] = {
            "sample_renderer_version": 1,
            "code": code,
            "packages": packages,
            "source": material,
            "reference_asset": reference,
            "image_asset": asset,
            "style_id": row["style_id"],
            "font_sha256": digest_file(font),
            "layout_overrides": row["layout_overrides"],
            "draft": plan["draft"],
            "width": 1080,
            "height": 1440,
        }
    if ledger is None:
        with Ledger(run / ".state/ledger.sqlite") as current:
            issues = allocation_summary(contract, current.budget())["allocation_issues"]
    else:
        issues = allocation_summary(contract, ledger.budget())["allocation_issues"]
    if issues:
        raise ValueError("sample task budget invalid: " + "; ".join(issues))
    return {"schema_version": 1, "request": plan, "styles": rows}


def _cache_valid(path: Path, signature: str) -> bool:
    try:
        report = load_json(path.with_suffix(".layout.json"))
        preview = path.with_name(path.stem + "-preview.png")
        return (
            report.get("sample_input_fingerprint") == signature
            and digest_file(path) == report["output_sha256"]
            and digest_file(preview) == report["preview_sha256"]
        )
    except (ValueError, OSError, KeyError):
        return False


def _render_one(run, plan, row, inputs):
    signature = fingerprint(inputs)
    cache = run / ".state/sample-renders"
    output = cache / (row["style_id"] + "-" + signature[:20] + ".png")
    attempt = 1
    while output.exists() and not _cache_valid(output, signature):
        attempt += 1
        output = cache / (row["style_id"] + "-" + signature[:20] + f"-attempt{attempt}.png")
    reused = _cache_valid(output, signature)
    if not reused:
        source = inputs["source"]
        quote = source["quote"]
        character = source["character"]
        report = layout.compose(
            contained(run, inputs["image_asset"]["path"]),
            output,
            text=quote["display_text"],
            character=character["name"],
            title=source["book"]["title"],
            chapter=quote["source"]["chapter"],
            font_path=Path(row["font"]),
            style_id=row["style_id"],
            draft=plan["draft"],
            rewrite=quote["text_mode"] == "rewrite",
            overrides=row["layout_overrides"],
        )
        preview = output.with_name(output.stem + "-preview.png")
        if (
            report["font_sha256"] != inputs["font_sha256"]
            or report["background_sha256"] != inputs["image_asset"]["sha256"]
        ):
            raise ValueError(
                "font/background changed during sample composition; preserve untracked output"
            )
        report.update(sample_input_fingerprint=signature, preview_sha256=digest_file(preview))
        save_json(output.with_suffix(".layout.json"), report)
    return output, reused


def _seal(folder: Path, signature: str):
    files = [
        {"path": p.relative_to(folder).as_posix(), "sha256": digest_file(p)}
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    ]
    save_json(
        folder / "build-seal.json",
        {"schema_version": 1, "input_fingerprint": signature, "files": files},
    )


def _check_files(folder: Path, signature: str) -> dict:
    seal = load_json(folder / "build-seal.json")
    if seal.get("schema_version") != 1 or seal.get("input_fingerprint") != signature:
        raise ValueError("managed sample seal differs from current inputs")
    names = [row["path"] for row in seal["files"]]
    expected = {
        style + suffix
        for style in STYLE_LABELS
        for suffix in [".png", "-preview.png", ".layout.json"]
    } | {"manifest.json", "six-styles-contact.png"}
    if len(names) != len(set(names)) or set(names) != expected:
        raise ValueError("managed sample seal has incomplete or duplicate files")
    if {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()} != expected | {
        "build-seal.json"
    }:
        raise ValueError("managed sample directory has unsealed files")
    for row in seal["files"]:
        if digest_file(contained(folder, row["path"])) != row["sha256"]:
            raise ValueError(
                "managed sample file damaged; preserve it and use a new output version"
            )
        if row["path"].endswith(".png"):
            with Image.open(contained(folder, row["path"])) as picture:
                picture.verify()
    manifest = load_json(folder / "manifest.json")
    if manifest.get("managed", {}).get("input_fingerprint") != signature:
        raise ValueError("managed sample manifest fingerprint mismatch")
    return manifest


def validate_managed_samples(run: Path, folder: Path, *, ledger: Ledger | None = None) -> dict:
    folder = contained(run, folder.resolve().relative_to(run.resolve()).as_posix())
    manifest = load_json(folder / "manifest.json")
    signature = manifest["managed"]["input_fingerprint"]
    if not isinstance(signature, str) or not re.fullmatch("[0-9a-f]{64}", signature):
        raise ValueError("invalid managed sample input identity")
    previous = load_json(run / ".state/sample-inputs" / (signature + ".json"))
    current = _inputs(run, previous["request"], ledger=ledger)
    if fingerprint(current) != signature or current != previous:
        raise ValueError("managed sample inputs changed; render a new version")
    _check_files(folder, signature)
    return {
        "input_fingerprint": signature,
        "styles": 6,
        "state": "valid",
        "scope": "File/dependency integrity only; human judgment pending.",
    }


def render_samples(run: Path, request: dict, output: Path, *, resume: bool = False) -> dict:
    recover_documents(run)
    run = run.resolve()
    output = output.resolve()
    if (
        output.parent != run
        or not re.fullmatch("[A-Za-z0-9][A-Za-z0-9_-]*", output.name)
        or output.name in {"images", "posters", "source", "data"}
    ):
        raise ValueError("samples output must be a new named immediate child of run")
    inputs = _inputs(run, request)
    signature = fingerprint(inputs)
    plan = inputs["request"]
    snapshots = run / ".state/sample-inputs"
    snapshot = snapshots / (signature + ".json")
    if snapshot.exists() and load_json(snapshot) != inputs:
        raise ValueError("sample input checkpoint changed; preserve evidence")
    if not snapshot.exists():
        save_json(snapshot, inputs)
    staging_root = run / ".sample-staging"
    staging_root.mkdir(exist_ok=True)
    stage = staging_root / (output.name + "-" + signature[:20])
    with Ledger(staging_root / "coordination.sqlite") as lock, lock.db:
        lock.db.execute("UPDATE config SET value=value WHERE key='limit'")
        if output.exists():
            if not resume:
                raise ValueError("samples output already exists; use --resume or a new version")
            validate_managed_samples(run, output)
            if load_json(output / "manifest.json")["managed"]["input_fingerprint"] != signature:
                raise ValueError(
                    "sample plan changed; preserve old output and choose a new version"
                )
            return {
                "output": str(output),
                "rendered": 0,
                "reused": 6,
                "published_reused": True,
                "image_tool_calls": 0,
                "input_fingerprint": signature,
            }
        if stage.exists() and not resume:
            raise ValueError("unfinished sample checkpoint exists; use --resume")
        stage.mkdir(exist_ok=True)
        try:
            release = stage / ("release-" + uuid.uuid4().hex)
            release.mkdir()
            rows = []
            rendered = 0
            reused = 0
            for row in plan["styles"]:
                style = row["style_id"]
                item = inputs["styles"][style]
                cached, hit = _render_one(run, plan, row, item)
                reused += hit
                rendered += not hit
                destination = release / (style + ".png")
                copy_atomic(cached, destination)
                copy_atomic(
                    cached.with_name(cached.stem + "-preview.png"),
                    release / (style + "-preview.png"),
                )
                report = load_json(cached.with_suffix(".layout.json"))
                report["preview"] = style + "-preview.png"
                save_json(destination.with_suffix(".layout.json"), report)
                asset = item["image_asset"]
                rows.append(
                    {
                        "style_id": style,
                        "path": destination.name,
                        "asset_id": asset["asset_id"],
                        "call_id": asset.get("call_id"),
                        "background_sha256": asset["sha256"],
                        "poster_sha256": report["output_sha256"],
                        "font_sha256": item["font_sha256"],
                        "font_license_note": row["font_license_note"],
                        "reuse_reason": row["reuse_reason"],
                        "layout_overrides": row["layout_overrides"],
                        "sample_input_fingerprint": fingerprint(item),
                        "font_size": report["font_size"],
                        "image_fit": report["image_fit"],
                    }
                )
            first = inputs["styles"][plan["styles"][0]["style_id"]]
            source = first["source"]
            sheet = Image.new("RGB", (1152, 1084), "#d6d3cd")
            draw = ImageDraw.Draw(sheet)
            small = ImageFont.truetype(plan["styles"][0]["font"], 18)
            for i, row in enumerate(rows):
                x = 18 + (i % 3) * 378
                y = 18 + (i // 3) * 533
                with Image.open(release / (row["style_id"] + "-preview.png")) as picture:
                    sheet.paste(picture, (x, y))
                draw.text((x, y + 486), STYLE_LABELS[row["style_id"]], font=small, fill="#222222")
            sheet.save(release / "six-styles-contact.png")
            save_json(
                release / "manifest.json",
                {
                    "schema_version": 1,
                    "source_hash": plan["source_hash"],
                    "quote_id": plan["quote_id"],
                    "quote_exact": source["quote"]["quote_exact"],
                    "character_id": plan["character_id"],
                    "reference_sha256": first["reference_asset"]["sha256"],
                    "styles": rows,
                    "human_review": "pending",
                    "ai_review": "pending",
                    "managed": {
                        "schema_version": 1,
                        "input_fingerprint": signature,
                        "created_at": now(),
                        "image_tool_calls": 0,
                        "physical_fonts_included": False,
                        "scope": "Supplementary samples; not main cards or approved publication.",
                    },
                },
            )
            _seal(release, signature)
            _check_files(release, signature)
            if fingerprint(_inputs(run, request)) != signature:
                raise ValueError(
                    "sample inputs changed before publication; preserve checkpoint and regenerate"
                )
            # Verify both absolute Windows move targets immediately before publication.
            if (
                not release.resolve().is_relative_to(stage.resolve())
                or stage.resolve().parent != staging_root.resolve()
                or output.resolve().parent != run
            ):
                raise ValueError("unsafe sample publication paths")
            if output.exists():
                raise ValueError("samples output appeared; nothing overwritten")
            os.rename(release, output)
            return {
                "output": str(output),
                "rendered": rendered,
                "reused": reused,
                "published_reused": False,
                "image_tool_calls": 0,
                "input_fingerprint": signature,
            }
        except Exception as error:
            save_json(
                stage / ("failure-" + uuid.uuid4().hex + ".json"),
                {"at": now(), "type": type(error).__name__, "note": str(error)},
            )
            raise
