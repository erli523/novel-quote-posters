"""Version-bound, recoverable application edits; no original-span mutation or image call."""

import copy
from pathlib import Path

from .documents import commit_documents, recover_documents
from .layout import STYLES
from .quotes import check_display
from .source import validate_source
from .storage import fingerprint, load_json, now, save_json
from .workflow import Ledger

DOCUMENTS = ["quotes.json", "characters.json", "cards.json"]


def _read(run: Path) -> tuple[dict, dict]:
    book = load_json(run / "book.json")
    validate_source(run, book)
    values = {name: load_json(run / name) if (run / name).exists() else None for name in DOCUMENTS}
    if not isinstance(values["quotes.json"], list) or not isinstance(values["characters.json"], dict):
        # A malformed persisted document is a CLI data error, not a Python argument error.
        raise ValueError("an analyzed source is required before edits")  # noqa: TRY004
    if values["cards.json"] is not None and not isinstance(values["cards.json"], list):
        raise ValueError("invalid cards document")
    return book, values


def _signature(book: dict, values: dict) -> str:
    return fingerprint({"edit_policy_version": 1, "book": book, "documents": values})


def edit_targets(run: Path) -> dict:
    recover_documents(run)
    book, values = _read(run)
    return {"schema_version": 1, "source_hash": book["source_hash"],
            "expected_fingerprint": _signature(book, values), "editor": "", "reason": "",
            "operations": [], "available": {
                "quotes": [{"quote_id": q["quote_id"], "quote_exact": q["quote_exact"],
                            "display_text": q["display_text"], "text_mode": q["text_mode"],
                            "speaker": q["speaker"]} for q in values["quotes.json"]],
                "cards": values["cards.json"] or []}}


def _fields(operation: dict, names: set[str]):
    if not isinstance(operation, dict) or set(operation) != names | {"type"}:
        raise ValueError("edit operation has missing or unsupported fields")


def _pending(card: dict, reason: str):
    card.pop("poster", None)  # Old version files stay intact in the run and journal.
    card["review"] = {"status": "pending", "reason": reason}


def _apply(book: dict, values: dict, operations: list, allow_rewrite: bool) -> list:
    quotes = {q["quote_id"]: q for q in values["quotes.json"]}
    cards = {c["card_id"]: c for c in values["cards.json"] or []}
    if len(quotes) != len(values["quotes.json"]) or len(cards) != len(values["cards.json"] or []):
        raise ValueError("duplicate quote/card IDs")
    impacts = []
    for operation in operations:
        kind = operation.get("type") if isinstance(operation, dict) else None
        if kind == "set-display-text":
            _fields(operation, {"quote_id", "display_text", "text_mode"})
            quote = quotes[operation["quote_id"]]
            if not isinstance(operation["display_text"], str) or not operation["display_text"].strip():
                raise ValueError("display text must be a nonempty string")
            candidate = {**quote, "display_text": operation["display_text"], "text_mode": operation["text_mode"]}
            check_display(book, candidate, allow_rewrite)
            if (quote["display_text"], quote["text_mode"]) == (candidate["display_text"], candidate["text_mode"]):
                continue
            quote.update(display_text=candidate["display_text"], text_mode=candidate["text_mode"],
                         review={"status": "pending", "reason": "display_text_edited"})
            affected = [card for card in cards.values() if card["quote_id"] == quote["quote_id"]]
            for card in affected:
                _pending(card, "display_text_edited")
            impacts.append({"type": kind, "quote_id": quote["quote_id"],
                            "invalidated_cards": [c["card_id"] for c in affected], "reused_imagery": True})
        elif kind == "bind-card":
            _fields(operation, {"card_id", "quote_id"})
            card, quote = cards[operation["card_id"]], quotes[operation["quote_id"]]
            check_display(book, quote, allow_rewrite)
            character_id = quote["speaker"]["character_id"]
            if (character_id not in values["characters.json"]
                    or quote["speaker"]["status"] not in {"explicit", "context_inferred"}
                    or quote["quote_type"] not in {"dialogue", "monologue"}):
                raise ValueError("cannot bind a named card to ambiguous or narrative text")
            if card["quote_id"] == quote["quote_id"] and card["character_id"] == character_id:
                continue
            changed_character = card["character_id"] != character_id
            card.update(quote_id=quote["quote_id"], character_id=character_id)
            if changed_character:
                for key in ["background", "image_asset_id"]:
                    card.pop(key, None)
                card["image_kind"] = "pending"
            _pending(card, "quote_or_character_rebound")
            impacts.append({"type": kind, "invalidated_cards": [card["card_id"]],
                            "reused_imagery": not changed_character, "character_changed": changed_character})
        elif kind == "set-style":
            _fields(operation, {"card_id", "style_id"})
            card = cards[operation["card_id"]]
            if operation["style_id"] not in STYLES:
                raise ValueError("unknown style")
            if card["style_id"] == operation["style_id"]:
                continue
            card["style_id"] = operation["style_id"]
            card.pop("layout_overrides", None)
            _pending(card, "style_edited")
            impacts.append({"type": kind, "invalidated_cards": [card["card_id"]],
                            "reused_imagery": True, "old_style_overrides_cleared": True})
        elif kind == "reorder-cards":
            _fields(operation, {"card_ids"})
            order = operation["card_ids"]
            if (not isinstance(order, list) or any(not isinstance(i, str) for i in order)
                    or len(order) != len(cards) or len(set(order)) != len(order) or set(order) != set(cards)):
                raise ValueError("card order must contain every current card exactly once")
            if [c["card_id"] for c in values["cards.json"] or []] == order:
                continue
            values["cards.json"] = [cards[identity] for identity in order]
            impacts.append({"type": kind, "invalidated_cards": [], "album_order_changed": True,
                            "reused_imagery": True})
        else:
            raise ValueError("unsupported edit operation; original source spans are immutable")
    return impacts


def apply_edits(run: Path, request: dict, *, allow_rewrite: bool = False) -> dict:
    recover_documents(run)
    if (request.get("schema_version") != 1 or not isinstance(request.get("editor"), str)
            or not request["editor"].strip() or not isinstance(request.get("reason"), str)
            or not request["reason"].strip() or not isinstance(request.get("operations"), list)
            or not request["operations"]):
        raise ValueError("edits require schema, named editor, reason and nonempty operations")
    request_hash = fingerprint({"request": request, "allow_rewrite": allow_rewrite})
    reason = "application_edits:" + request_hash
    history = run / ".state" / "edits" / (request_hash + ".json")
    book, before = _read(run)
    current = _signature(book, before)
    if history.exists():
        previous = load_json(history)
        if (previous.get("request") != request or previous.get("allow_rewrite") != allow_rewrite
                or previous.get("result") != {"request_hash": request_hash, "impacts": previous.get("impacts"),
                    "image_tool_calls": 0, "original_source_spans_changed": 0}):
            raise ValueError("edit plan history differs from this request; preserve it for inspection")
        completed = [p for p in (run / ".state/transactions").glob("*.json")
                     if load_json(p).get("reason") == reason]
        if current == previous["after_fingerprint"] and (completed or previous.get("no_document_changes")):
            return {**previous["result"], "transaction": completed[-1].relative_to(run).as_posix() if completed else None,
                    "reused": True}
        if current != previous["before_fingerprint"]:
            raise ValueError("old edit request cannot overwrite later changes; prepare a fresh request")
    if request.get("source_hash") != book["source_hash"] or request.get("expected_fingerprint") != current:
        raise ValueError("edit targets changed; prepare a fresh snapshot")
    after = copy.deepcopy(before)
    try:
        impacts = _apply(book, after, request["operations"], allow_rewrite)
    except (KeyError, TypeError) as error:
        raise ValueError("invalid or unknown edit target") from error
    result = {"request_hash": request_hash, "impacts": impacts, "image_tool_calls": 0,
              "original_source_spans_changed": 0}
    changed = {name: value for name, value in after.items() if value != before[name]}
    record = {"at": now(), "request": request, "allow_rewrite": allow_rewrite,
              "before_fingerprint": current, "after_fingerprint": _signature(book, after),
              "impacts": impacts, "result": result, "no_document_changes": not changed}
    # Concurrent planners must not overwrite the first immutable request record.
    with Ledger(run / ".state/ledger.sqlite") as ledger, ledger.db:
        ledger.db.execute("UPDATE config SET value=value WHERE key='limit'")
        if history.exists():
            previous = load_json(history)
            if {k: v for k, v in previous.items() if k != "at"} != {k: v for k, v in record.items() if k != "at"}:
                raise ValueError("edit plan history differs from this request; preserve it for inspection")
        else:
            save_json(history, record)
    # Check all dependency documents while holding the same lock as the atomic commit.
    validate_source(run, book)
    transaction = commit_documents(run, changed, reason=reason,
                                   expected={**before, "book.json": book}) if changed else None
    return {**result, "transaction": transaction, "reused": False}
