"""Application-tool reservations and provenance import, without making image API calls."""

import re
import uuid
from pathlib import Path

from PIL import Image

from .documents import commit_documents, recover_documents
from .quotes import check_display
from .source import validate_source
from .storage import contained, copy_atomic, digest_file, fingerprint, load_json, save_json
from .workflow import Ledger

TOOL = "image_gen.imagegen"


def plan_image(run: Path, request: dict) -> dict:
    recover_documents(run)
    identity = request["call_id"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", identity):
        raise ValueError("invalid image call ID")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", request.get("asset_id", "")):
        raise ValueError("a stable ASCII image asset ID is required")
    if request.get("tool") != TOOL or request["kind"] not in {"reference", "background", "edit"}:
        raise ValueError("V1 only records the approved application image tool")
    if not isinstance(request.get("prompt"), str) or not request["prompt"].strip():
        raise ValueError("image prompt is empty")
    book = load_json(run / "book.json")
    validate_source(run, book)
    characters = load_json(run / "characters.json")
    character = characters[request["character_id"]]
    references = request.get("references", [])
    if request["kind"] == "edit" and not references:
        raise ValueError("image editing requires a reference image")
    reference_hashes = {}
    for relative in references:
        path = contained(run, relative)
        with Image.open(path) as picture:
            picture.verify()
        reference_hashes[relative] = digest_file(path)
    payload = {"schema_version": 1, "request": request, "source_hash": book["source_hash"],
               "character_snapshot": character, "reference_hashes": reference_hashes}
    input_hash = fingerprint(payload)
    snapshot = run / ".state" / "image-requests" / f"{input_hash}.json"
    if not snapshot.exists():
        save_json(snapshot, payload)
    elif load_json(snapshot) != payload:
        raise ValueError("image request snapshot damaged")
    with Ledger(run / ".state" / "ledger.sqlite") as ledger:
        budget = ledger.budget()
        existing = next((c for c in budget["calls"] if c["call_id"] == identity), None)
        if not existing and request.get("retry_of"):
            parent = next((c for c in budget["calls"] if c["call_id"] == request["retry_of"]), None)
            if not parent or parent["status"] != "failed" or (parent["result"] or {}).get("failure_kind") not in {"transient", "not_invoked"}:
                raise ValueError("retry requires a confirmed transient failure, never an unknown outcome")
            retries = [c for c in budget["calls"] if c["request"] and
                       c["request"].get("request", {}).get("retry_of") == request["retry_of"]]
            if retries:
                raise ValueError("one retry per failed call; use a new candidate within the total budget")
            original = parent["request"]["request"] if parent["request"] else {}
            if original.get("retry_of"):
                raise ValueError("cannot retry a retry; per-asset retry limit reached")
            fields = ["asset_id", "character_id", "tool", "prompt", "kind", "references"]
            if any(original.get(key) != request.get(key) for key in fields) or parent["request"].get("character_snapshot") != character or parent["request"].get("reference_hashes") != reference_hashes:
                raise ValueError("retry inputs differ from failed request")
        elif not existing:
            related = [c for c in budget["calls"] if c["request"] and
                       c["request"].get("request", {}).get("asset_id") == request["asset_id"]]
            if any(c["status"] in {"reserved", "unknown_outcome"} for c in related):
                raise ValueError("reconcile the existing asset call before planning another")
            if related and not request.get("new_candidate_reason"):
                raise ValueError("a new candidate needs an explicit reason or bounded retry_of")
        result = ledger.reserve(identity, input_hash, request=payload)
        return {**result, "call_id": identity, "input_hash": input_hash,
                "request_snapshot": snapshot.relative_to(run).as_posix(),
                "remaining": ledger.budget()["remaining"], "tool_was_called": False}


def record_failure(run: Path, call_id: str, status: str, *, failure_kind: str, note: str) -> dict:
    if status not in {"failed", "unknown_outcome", "tool_unavailable"}:
        raise ValueError("successful outputs must be registered with their actual image file")
    if failure_kind not in {"transient", "not_invoked", "quota", "policy", "unavailable", "unknown", "input"} or not note.strip():
        raise ValueError("failure classification and note are required")
    recover_documents(run)
    with Ledger(run / ".state" / "ledger.sqlite") as ledger:
        ledger.resolve_call(call_id, status, details={"failure_kind": failure_kind, "note": note})
        return {"call_id": call_id, "status": status, "remaining": ledger.budget()["remaining"]}


def budget_summary(run: Path) -> dict:
    recover_documents(run)
    with Ledger(run / ".state" / "ledger.sqlite") as ledger:
        data = ledger.budget()
        # Normal status output intentionally excludes prompts, raw responses and private paths.
        return {**{k: v for k, v in data.items() if k != "calls"},
                "calls": [{k: c[k] for k in ["call_id", "status", "input_hash", "output"]}
                          for c in data["calls"]]}


def register_image(run: Path, source: Path, receipt: dict) -> dict:
    recover_documents(run)
    kind = receipt["source_kind"]
    if kind not in {"generated", "user_provided", "test_fixture"}:
        raise ValueError("unknown image source kind")
    characters = load_json(run / "characters.json")
    if receipt["character_id"] not in characters:
        raise ValueError("unknown image character")
    source_hash = digest_file(source)
    if receipt["image_sha256"] != source_hash:
        raise ValueError("image receipt hash differs from actual file")
    with Image.open(source) as picture:
        size, image_format = picture.size, picture.format
        picture.verify()
    if image_format not in {"PNG", "JPEG", "WEBP"}:
        raise ValueError("image must be PNG, JPEG or WEBP")
    asset_id = "asset_" + fingerprint(receipt)[:24]
    with Ledger(run / ".state" / "ledger.sqlite") as ledger:
        call = None
        if kind == "generated":
            call = next((c for c in ledger.budget()["calls"] if c["call_id"] == receipt["call_id"]), None)
            if not call or not call["request"] or call["request"].get("request", {}).get("tool") != TOOL:
                raise ValueError("generated imagery needs an actual reserved application request")
            if call["status"] not in {"reserved", "unknown_outcome", "succeeded"}:
                raise ValueError("failed terminal call cannot be turned into an image result")
            planned = call["request"]["request"]
            if planned["character_id"] != receipt["character_id"] or receipt["input_hash"] != call["input_hash"]:
                raise ValueError("image receipt does not match reserved character/inputs")
            if not receipt.get("tool_output_reference"):
                raise ValueError("application output reference is required; use unavailable if not exposed")
            if call["status"] == "succeeded" and call["output"] != "image:" + asset_id:
                raise ValueError("successful call already has a different registered output")
        elif not receipt.get("source_note"):
            raise ValueError("user/test image requires a source note")
        suffix = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[image_format]
        target = run / "images" / f"{source_hash}{suffix}"
        if target.exists() and digest_file(target) != source_hash:
            damaged = target.with_name(f"damaged-{uuid.uuid4().hex}{suffix}")
            if target.resolve().parent != (run / "images").resolve() or damaged.resolve().parent != target.resolve().parent:
                raise ValueError("unsafe image quarantine path")
            target.rename(damaged)
        if not target.exists():
            copy_atomic(source, target)
        if digest_file(target) != source_hash:
            raise ValueError("stored image is damaged; preserve evidence and import a new version")
        receipt_path = run / ".state" / "image-receipts" / f"{asset_id}.json"
        if not receipt_path.exists():
            save_json(receipt_path, receipt)
        elif load_json(receipt_path) != receipt:
            raise ValueError("image receipt damaged")
        assets_path = run / "image-assets.json"
        assets = load_json(assets_path) if assets_path.exists() else {}
        record = {"asset_id": asset_id, "path": target.relative_to(run).as_posix(),
                  "sha256": source_hash, "source_kind": kind, "character_id": receipt["character_id"],
                  "width": size[0], "height": size[1], "format": image_format,
                  "receipt": receipt_path.relative_to(run).as_posix(), "receipt_sha256": digest_file(receipt_path),
                  "call_id": receipt.get("call_id"), "input_hash": receipt.get("input_hash"),
                  "request_kind": call["request"]["request"]["kind"] if call else None,
                  "tool": TOOL if call else None, "model_version": "unavailable",
                  "seed": "unavailable", "cost": "unavailable",
                  "evidence_type": "application_attestation" if call else "source_declaration"}
        if asset_id in assets and assets[asset_id] != record:
            raise ValueError("asset registration conflicts with immutable record")
        ledger.put("image:" + asset_id, target)
        if asset_id not in assets:
            assets[asset_id] = record
            commit_documents(run, {"image-assets.json": assets}, reason="image_registered")
        if call:
            ledger.resolve_call(receipt["call_id"], "succeeded", "image:" + asset_id,
                                details={"receipt_sha256": record["receipt_sha256"]})
        return record


def validate_image_asset(run: Path, card: dict, *, required: bool = False) -> dict | None:
    identity = card.get("image_asset_id")
    if not identity:
        if required:
            raise ValueError("final export requires registered image provenance, not just image_kind")
        return None
    asset = load_json(run / "image-assets.json")[identity]
    background = contained(run, card["background"])
    registered = contained(run, asset["path"])
    receipt_path = contained(run, asset["receipt"])
    receipt = load_json(receipt_path)
    if (digest_file(background) != asset["sha256"] or digest_file(registered) != asset["sha256"]
            or digest_file(receipt_path) != asset["receipt_sha256"]
            or asset["character_id"] != card["character_id"] or asset["source_kind"] != card.get("image_kind")
            or asset["sha256"] != receipt["image_sha256"]
            or asset["source_kind"] != receipt["source_kind"]
            or asset["character_id"] != receipt["character_id"]
            or identity != "asset_" + fingerprint(receipt)[:24]):
        raise ValueError("registered image provenance changed or does not match this card")
    if asset["source_kind"] == "generated":
        with Ledger(run / ".state" / "ledger.sqlite") as ledger:
            call = next((c for c in ledger.budget()["calls"] if c["call_id"] == asset["call_id"]), None)
            if not call or call["status"] != "succeeded" or call["input_hash"] != asset["input_hash"] or call["output"] != "image:" + identity or not ledger.valid(call["output"]) or (call["result"] or {}).get("receipt_sha256") != asset["receipt_sha256"]:
                raise ValueError("generated image result has not been reconciled to a successful call")
    return asset


def validate_character_reference(run: Path, character: dict, *, required: bool = False) -> dict | None:
    """Check the baseline's own provenance, rather than trusting a reference filename."""
    identity, path = character.get("reference_asset_id"), character.get("reference_image")
    if not identity or not path:
        if required:
            raise ValueError("generated character needs a registered baseline reference for visual comparison")
        return None
    asset = load_json(run / "image-assets.json")[identity]
    result = validate_image_asset(run, {"background": path, "image_asset_id": identity,
        "image_kind": asset["source_kind"], "character_id": character["character_id"]}, required=True)
    if required and result["source_kind"] not in {"generated", "user_provided"}:
        raise ValueError("test fixtures cannot be the baseline reference for a generated final card")
    return result


def import_legacy_failures(run: Path, path: Path) -> dict:
    """Preserve prior conservative call counts; never infer success without an asset."""
    recover_documents(run)
    data = load_json(path)
    rows = data["calls"]
    if any(row["state"] not in {"failed", "tool_unavailable", "unknown_outcome", "reserved"} for row in rows):
        raise ValueError("legacy success needs actual image reconciliation, not a log-only import")
    with Ledger(run / ".state" / "ledger.sqlite") as ledger:
        for row in rows:
            payload = {"origin": "legacy_failure_log", "record": row}
            ledger.reserve(row["call_id"], fingerprint(payload), request=payload)
            if row["state"] != "reserved":
                ledger.resolve_call(row["call_id"], row["state"], details={
                    "failure_kind": "unavailable" if row["state"] == "tool_unavailable" else "unknown",
                    "note": row.get("error", "historical call record")})
        return {"imported": len(rows), "budget": budget_summary(run)}


def prepare_cards(run: Path, *, count: int = 10, style_id: str = "mist_cinema") -> dict:
    from .layout import STYLES
    if count <= 0 or style_id not in STYLES:
        raise ValueError("invalid count or style")
    recover_documents(run)
    if (run / "cards.json").exists():
        raise ValueError("cards already exist; update their stable IDs rather than replacing them")
    book = load_json(run / "book.json")
    validate_source(run, book)
    quotes = load_json(run / "quotes.json")
    characters = load_json(run / "characters.json")
    selected = [q for q in quotes if q.get("selection", {}).get("selected")
                and q["speaker"]["status"] in {"explicit", "context_inferred"}
                and q["speaker"]["character_id"] in characters]
    selected.sort(key=lambda q: -(q.get("selection", {}).get("score") or 0))
    cards = []
    for quote in selected[:count]:
        check_display(book, quote)
        cards.append({"card_id": f"card{len(cards)+1:03d}", "quote_id": quote["quote_id"],
                      "character_id": quote["speaker"]["character_id"], "style_id": style_id,
                      "image_kind": "pending", "review": {"status": "pending"}})
    if cards:
        commit_documents(run, {"cards.json": cards}, reason="selected_quotes_to_cards")
    return {"requested": count, "prepared": len(cards), "shortfall": max(0, count-len(cards)),
            "reason": "only source-verified, application-selected quotes; no invented fillers"}


def attach_image(run: Path, asset_id: str, card_id: str) -> dict:
    recover_documents(run)
    assets = load_json(run / "image-assets.json")
    asset = assets[asset_id]
    cards = load_json(run / "cards.json")
    card = next((c for c in cards if c["card_id"] == card_id), None)
    if card is None:
        raise ValueError("unknown card ID")
    candidate = {**card, "background": asset["path"], "image_kind": asset["source_kind"],
                 "image_asset_id": asset_id}
    validate_image_asset(run, candidate, required=True)
    if all(card.get(key) == candidate[key] for key in ["background", "image_kind", "image_asset_id"]):
        return card
    card.update(background=asset["path"], image_kind=asset["source_kind"], image_asset_id=asset_id,
                review={"status": "pending", "reason": "background_changed"})
    commit_documents(run, {"cards.json": cards}, reason="attach_image_to_card")
    return card


def set_reference(run: Path, asset_id: str, character_id: str) -> dict:
    recover_documents(run)
    asset = load_json(run / "image-assets.json")[asset_id]
    validate_image_asset(run, {"background": asset["path"], "image_kind": asset["source_kind"],
                               "image_asset_id": asset_id, "character_id": character_id}, required=True)
    characters = load_json(run / "characters.json")
    character = characters[character_id]
    if character.get("reference_asset_id") == asset_id:
        return character
    character.update(reference_image=asset["path"], reference_asset_id=asset_id,
                     visual_anchor_version=character.get("visual_anchor_version", 0)+1)
    commit_documents(run, {"characters.json": characters}, reason="character_reference_changed")
    return character
