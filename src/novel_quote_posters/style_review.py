"""Version-bound supplementary six-style review; never approves quotes or main cards."""

import base64
import io
import json
from datetime import datetime
from itertools import combinations
from pathlib import Path

from PIL import Image, ImageCms

from . import layout, layout_options
from .contracts import allocation_summary, load_contract, selection_issues
from .documents import commit_documents, recover_documents
from .images import validate_character_reference, validate_image_asset
from .quotes import check_display
from .review import STYLE_LABELS
from .source import validate_source
from .source_context import SourceContext
from .storage import contained, digest_file, fingerprint, load_json, now, save_json, sha256
from .workflow import Ledger

POLICY_VERSION = 1
TARGET_DOCUMENT = "six-style-target.json"
REVIEW_DOCUMENT = "six-style-review.json"
CHECKS = {
    "text": "逐字核对原句、人物、作品名及标签",
    "identity": "对照基准与其他样张，人物外貌及时代可接受",
    "mobile": "360×480下正文、署名可读，没有文字遮脸",
    "crop": "查看方形模拟，接受本次仅完整3:4交付",
    "style_context": "对照上下文，风格、单人主体及画面与语句关系合适；主题化配图不冒充场景还原",
}
DIMENSIONS = {
    "composition": "构图",
    "typography": "排字",
    "whitespace": "留白",
    "background": "背景",
    "palette": "配色",
}
STATES = {"pending", "approved", "rejected"}


def _optional(run, name):
    path = run / name
    return load_json(path) if path.exists() else None


def _png(raw, size):
    with Image.open(io.BytesIO(raw)) as picture:
        if picture.format != "PNG" or picture.size != size or not picture.info.get("icc_profile"):
            raise ValueError("six-style samples require sRGB PNG at 1080x1440")
        profile = ImageCms.ImageCmsProfile(io.BytesIO(picture.info["icc_profile"]))
        if "srgb" not in ImageCms.getProfileDescription(profile).lower():
            raise ValueError("six-style sample profile is not sRGB")
        picture.load()
        return picture.copy(), picture.info["icc_profile"]


def _views(raw, reference):
    picture, icc = _png(raw, (1080, 1440))
    result = {"full": raw, "reference": reference}
    for key, view in [
        ("mobile", picture.resize((360, 480), Image.Resampling.LANCZOS)),
        ("center_square_simulation", picture.crop((0, 180, 1080, 1260))),
    ]:
        buffer = io.BytesIO()
        view.save(buffer, format="PNG", icc_profile=icc)
        result[key] = buffer.getvalue()
    return result


def _material(run: Path, samples: str, ledger: Ledger | None = None) -> tuple[dict, dict]:
    folder = contained(run, samples)
    if folder == run.resolve() or not folder.is_dir():
        raise ValueError("named samples directory inside run required")
    book = load_json(run / "book.json")
    validate_source(run, book)
    contract = load_contract(run, book)
    if contract is None:
        raise ValueError("six-style review requires explicit task contract")
    manifest_path = folder / "manifest.json"
    manifest = load_json(manifest_path)
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or manifest.get("source_hash") != book["source_hash"]
        or any(
            not isinstance(manifest.get(key), str)
            for key in ["quote_id", "quote_exact", "character_id"]
        )
    ):
        raise ValueError("samples source or schema changed")
    quotes = {row["quote_id"]: row for row in load_json(run / "quotes.json")}
    quote = quotes[manifest["quote_id"]]
    character_id = manifest["character_id"]
    character = load_json(run / "characters.json")[character_id]
    check_display(book, quote, allow_rewrite=contract["definition"]["allow_rewrite"])
    if (
        quote["quote_exact"] != manifest["quote_exact"]
        or quote["speaker"]["character_id"] != character_id
        or quote["speaker"]["status"] not in {"explicit", "context_inferred"}
        or quote["quote_type"] not in {"dialogue", "monologue"}
    ):
        raise ValueError("samples quote or named speaker changed")
    reference = validate_character_reference(run, character, required=True, ledger=ledger)
    reference_bytes = contained(run, character["reference_image"]).read_bytes()
    if (
        reference["sha256"] != manifest["reference_sha256"]
        or sha256(reference_bytes) != reference["sha256"]
    ):
        raise ValueError("samples baseline changed")
    rows = manifest["styles"]
    if (
        not isinstance(rows, list)
        or len(rows) != 6
        or any(
            not isinstance(row, dict) or not isinstance(row.get("style_id"), str) for row in rows
        )
        or {row["style_id"] for row in rows} != set(STYLE_LABELS)
    ):
        raise ValueError("exactly six unique style IDs required")
    assets = load_json(run / "image-assets.json")
    samples_material, views, issues = [], {}, []
    for row in sorted(rows, key=lambda r: list(STYLE_LABELS).index(r["style_id"])):
        style = row["style_id"]
        path = contained(folder, row["path"])
        report_path = path.with_suffix(".layout.json")
        report = load_json(report_path)
        raw = path.read_bytes()
        asset = assets[row["asset_id"]]
        card = {
            "card_id": style,
            "quote_id": quote["quote_id"],
            "character_id": character_id,
            "style_id": style,
            "background": asset["path"],
            "image_kind": asset["source_kind"],
            "image_asset_id": row["asset_id"],
        }
        validate_image_asset(run, card, required=True, ledger=ledger)
        issues.extend(selection_issues(contract, [card], quotes, check_count=False))
        if asset["source_kind"] not in {"generated", "user_provided"}:
            issues.append("test samples cannot receive real supplementary approval")
        if (
            sha256(raw) != row["poster_sha256"]
            or sha256(raw) != report["output_sha256"]
            or asset["sha256"] != row["background_sha256"]
            or asset["sha256"] != report["background_sha256"]
            or report["font_sha256"] != row["font_sha256"]
            or report["style_id"] != style
            or report["character"] != character["name"]
            or report["title"] != book["title"]
            or report["text"] != quote["display_text"]
            or report["rewrite"] != (quote["text_mode"] == "rewrite")
            or "".join(line["text"] for line in report["lines"]) != quote["display_text"]
            or report["style"]
            != layout.resolve_style(style, quote["display_text"], row.get("layout_overrides"))
        ):
            raise ValueError(
                "six-style sample image, layout or source differs; regenerate samples/review"
            )
        views[style] = _views(raw, reference_bytes)
        samples_material.append(
            {
                "style_id": style,
                "path": path.relative_to(run.resolve()).as_posix(),
                "poster_sha256": sha256(raw),
                "report_sha256": digest_file(report_path),
                "asset": asset,
                "font_sha256": report["font_sha256"],
                "draft": report["draft"],
                "view_hashes": {k: sha256(v) for k, v in views[style].items()},
                "crop_box": [0, 180, 1080, 1260],
            }
        )
    if ledger is None:
        with Ledger(run / ".state/ledger.sqlite") as current:
            issues.extend(allocation_summary(contract, current.budget())["allocation_issues"])
    else:
        issues.extend(allocation_summary(contract, ledger.budget())["allocation_issues"])
    material = {
        "policy_version": POLICY_VERSION,
        "samples": folder.relative_to(run.resolve()).as_posix(),
        "manifest_sha256": digest_file(manifest_path),
        "source_hash": book["source_hash"],
        "canonical_hash": book["canonical_hash"],
        "book_title": book["title"],
        "task_contract_fingerprint": contract["fingerprint"],
        "quote": {k: v for k, v in quote.items() if k != "review"},
        "character": character,
        "source_context": SourceContext(book).target(quote),
        "layout_code": {
            "layout": digest_file(Path(layout.__file__)),
            "options": digest_file(Path(layout_options.__file__)),
            "review": digest_file(Path(__file__)),
            "page": digest_file(Path(__file__).with_name("style_review_page.py")),
        },
        "styles": samples_material,
        "approval_issues": sorted(set(issues)),
        "claim_scope": "Supplementary sample comparison only; not attribution, main-card, album or release approval.",
    }
    return material, views


def style_review_targets(run: Path, samples: str) -> tuple[dict, dict]:
    recover_documents(run)
    material, views = _material(run, samples)
    previous = _optional(run, TARGET_DOCUMENT)
    signature = fingerprint(material)
    if (
        previous
        and previous.get("fingerprint") == signature
        and previous.get("samples") == material["samples"]
    ):
        registration = previous
    else:
        registration = {
            "schema_version": 1,
            "version": (previous["version"] + 1) if previous else 1,
            "samples": material["samples"],
            "fingerprint": signature,
        }
    form = {
        "schema_version": 2,
        "reviewer_type": "human",
        "reviewer": "",
        "reviewed_at": "",
        "attested": False,
        "prior_review_fingerprint": fingerprint(_optional(run, REVIEW_DOCUMENT)),
        "registration": registration,
        "target": {"material": material, "fingerprint": signature},
        "styles": [
            {
                "style_id": row["style_id"],
                "status": "pending",
                "checks": dict.fromkeys(CHECKS, "pending"),
                "note": "",
            }
            for row in material["styles"]
        ],
        "pairs": [
            {"styles": list(pair), "human_status": "pending", "human_dimensions": [], "note": ""}
            for pair in combinations(STYLE_LABELS, 2)
        ],
    }
    return form, views


def _decisions(record: dict) -> str:
    styles, pairs = record.get("styles"), record.get("pairs")
    if (
        not isinstance(styles, list)
        or len(styles) != 6
        or any(
            not isinstance(row, dict) or not isinstance(row.get("style_id"), str) for row in styles
        )
        or {row["style_id"] for row in styles} != set(STYLE_LABELS)
    ):
        raise ValueError("six complete unique style decisions required")
    for row in styles:
        checks = row.get("checks")
        if (
            not isinstance(row.get("status"), str)
            or row["status"] not in STATES
            or not isinstance(checks, dict)
            or set(checks) != set(CHECKS)
            or any(
                not isinstance(value, str) or value not in {"pending", "passed", "failed"}
                for value in checks.values()
            )
            or not isinstance(row.get("note"), str)
        ):
            raise ValueError("invalid style checklist")
        if row["status"] == "approved" and set(checks.values()) != {"passed"}:
            raise ValueError("approved style requires all checks passed")
    required = {frozenset(pair) for pair in combinations(STYLE_LABELS, 2)}
    seen = set()
    if not isinstance(pairs, list) or len(pairs) != 15:
        raise ValueError("15 unique pair decisions required")
    for row in pairs:
        if not isinstance(row, dict):
            raise ValueError("invalid pair decision")  # noqa: TRY004 - CLI record data error
        members, dimensions = row.get("styles"), row.get("human_dimensions")
        if (
            not isinstance(members, list)
            or len(members) != 2
            or any(not isinstance(s, str) for s in members)
            or frozenset(members) not in required
            or frozenset(members) in seen
            or not isinstance(dimensions, list)
            or any(not isinstance(d, str) for d in dimensions)
            or len(set(dimensions)) != len(dimensions)
            or not set(dimensions) <= set(DIMENSIONS)
            or not isinstance(row.get("human_status"), str)
            or row["human_status"] not in STATES
            or not isinstance(row.get("note"), str)
        ):
            raise ValueError("invalid or duplicate pair decision")
        seen.add(frozenset(members))
        if row["human_status"] == "approved" and len(dimensions) < 3:
            raise ValueError("approved pair requires at least three human-selected dimensions")
    states = [r["status"] for r in styles] + [r["human_status"] for r in pairs]
    return (
        "rejected"
        if "rejected" in states
        else "approved"
        if set(states) == {"approved"}
        else "pending"
    )


def _attestation(record):
    if (
        not isinstance(record, dict)
        or record.get("schema_version") != 2
        or record.get("reviewer_type") != "human"
        or record.get("attested") is not True
        or not isinstance(record.get("reviewer"), str)
        or not record["reviewer"].strip()
        or not isinstance(record.get("reviewed_at"), str)
    ):
        raise ValueError("current schema 2 and explicit named human attestation required")
    try:
        if datetime.fromisoformat(record["reviewed_at"]).utcoffset() is None:
            raise ValueError("timezone missing")
    except ValueError as error:
        raise ValueError("review time requires ISO 8601 timezone") from error


def apply_style_review(run: Path, record: dict) -> dict:
    recover_documents(run)
    _attestation(record)
    registered = _optional(run, TARGET_DOCUMENT)
    if not registered or record.get("registration") != registered:
        raise ValueError("six-style registration changed; generate a new review page")
    material, _ = _material(run, registered["samples"])
    target = {"material": material, "fingerprint": fingerprint(material)}
    if record.get("target") != target or registered["fingerprint"] != target["fingerprint"]:
        raise ValueError("six-style content changed; regenerate review page")
    state = _decisions(record)
    if material["approval_issues"] and (
        any(row["status"] == "approved" for row in record["styles"])
        or any(row["human_status"] == "approved" for row in record["pairs"])
    ):
        raise ValueError("task/provenance blocks supplementary approval")
    proposed = {"schema_version": 1, "status": state, "record": record}
    previous = _optional(run, REVIEW_DOCUMENT)
    if proposed != previous and record.get("prior_review_fingerprint") != fingerprint(previous):
        raise ValueError("later human decisions exist; generate a fresh review page")

    def guard(_ledger):
        current, _ = _material(run, registered["samples"], ledger=_ledger)
        if current != material:
            raise ValueError("six-style content changed during import; regenerate page")
        history = run / ".state/style-review-records" / (fingerprint(record) + ".json")
        if history.exists() and load_json(history)["record"] != record:
            raise ValueError("supplementary review history damaged; preserve evidence")
        if not history.exists():
            save_json(history, {"record": record, "imported_at": now()})

    transaction = (
        commit_documents(
            run,
            {REVIEW_DOCUMENT: proposed},
            reason="human_six_style_review",
            expected={TARGET_DOCUMENT: registered, REVIEW_DOCUMENT: previous},
            guard=guard,
        )
        if proposed != previous
        else None
    )
    return {
        "state": state,
        "reused": transaction is None,
        "transaction": transaction,
        "main_cards_changed": 0,
        "quote_approvals_changed": 0,
        "image_tool_calls": 0,
    }


def style_review_status(run: Path) -> dict:
    try:
        registered = _optional(run, TARGET_DOCUMENT)
        if not registered:
            return {"state": "not_started"}
        material, _ = _material(run, registered["samples"])
        if fingerprint(material) != registered["fingerprint"]:
            raise ValueError("samples or dependencies changed")
        stored = _optional(run, REVIEW_DOCUMENT)
        if not stored:
            return {
                "state": "pending",
                "fingerprint": registered["fingerprint"],
                "samples": registered["samples"],
            }
        record = stored["record"]
        _attestation(record)
        if record["registration"] != registered or record["target"] != {
            "material": material,
            "fingerprint": fingerprint(material),
        }:
            raise ValueError("stored review targets an older sample version")
        state = _decisions(record)
        if state == "approved" and material["approval_issues"]:
            raise ValueError("task/provenance changed")
        return {
            "state": state,
            "fingerprint": registered["fingerprint"],
            "samples": registered["samples"],
            "scope": material["claim_scope"],
        }
    except (ValueError, OSError, KeyError, TypeError) as error:
        return {"state": "invalidated", "reason": str(error)}


def write_style_review_page(run: Path, samples: str, output: Path) -> dict:
    from .style_review_page import PAGE

    if output.exists() or output.with_suffix(".targets.json").exists():
        raise ValueError("style review output exists; choose a new version")
    previous = _optional(run, TARGET_DOCUMENT)
    form, views = style_review_targets(run, samples)
    data = {
        "record": form,
        "labels": STYLE_LABELS,
        "checks": CHECKS,
        "dimensions": DIMENSIONS,
        "reference": "data:image/png;base64,"
        + base64.b64encode(next(iter(views.values()))["reference"]).decode("ascii"),
        "images": {
            style: {
                key: "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
                for key, raw in rows.items()
                if key != "reference"
            }
            for style, rows in views.items()
        },
    }
    encoded = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(PAGE.replace("__STYLE_DATA__", encoded), encoding="utf-8", newline="\n")
    save_json(output.with_suffix(".targets.json"), form)

    def guard(_ledger):
        current, _ = _material(run, samples, ledger=_ledger)
        if current != form["target"]["material"]:
            raise ValueError("samples changed while preparing review; keep evidence and regenerate")

    if previous != form["registration"]:
        commit_documents(
            run,
            {TARGET_DOCUMENT: form["registration"]},
            reason="prepare_six_style_review",
            expected={TARGET_DOCUMENT: previous},
            guard=guard,
        )
    else:
        guard(None)
    return {
        "styles": 6,
        "pairs": 15,
        "state": "pending",
        "fingerprint": form["target"]["fingerprint"],
        "human_approval_granted": False,
    }
