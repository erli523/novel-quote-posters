"""Explicit per-run intent, independent of current cards and without granting approval."""

import copy
import math
import re
from pathlib import Path

from .documents import commit_documents, recover_documents
from .layout import STYLES
from .source import validate_source
from .storage import fingerprint, load_json, now, save_json
from .workflow import Ledger

ALLOCATION_BUCKETS = ("reference", "background", "edit", "alternative", "retry", "unclassified", "unallocated")
LIVE_BUCKETS = ALLOCATION_BUCKETS[:5]


def default_allocation(limit: int) -> dict:
    """Planning suggestion only; never transfers capacity after a failed call."""
    result = dict.fromkeys(ALLOCATION_BUCKETS, 0)
    for bucket in ("reference", "background", "background", "background", "edit", "retry", "alternative"):
        if sum(result.values()) < limit:
            result[bucket] += 1
    result["background"] += max(0, limit-sum(result.values()))
    return result


def call_bucket(payload: dict | None) -> str:
    request = payload.get("request") if isinstance(payload, dict) else None
    if (not isinstance(request, dict) or request.get("tool") != "image_gen.imagegen"
            or request.get("kind") not in {"reference", "background", "edit"}):
        return "unclassified"
    if request.get("retry_of"):
        return "retry"
    # Legacy reason strings also described capability rechecks. Their prose is not a category.
    if request.get("purpose") == "alternative" or (payload.get("schema_version") == 2 and request.get("new_candidate_reason")):
        return "alternative"
    return request["kind"]


def allocation_summary(contract: dict | None, budget: dict) -> dict:
    used = dict.fromkeys(ALLOCATION_BUCKETS, 0)
    for call in budget["calls"]:
        used[call_bucket(call["request"])] += 1
    definition = contract["definition"] if contract else {}
    allocation = definition.get("image_call_allocation")
    issues = []
    if allocation is None:
        issues.append("explicit image call allocation missing; upgrade task contract before new calls/final approval")
    rows = {key: {"limit": allocation[key] if allocation else None, "used": used[key],
                  "remaining": max(0, allocation[key]-used[key]) if allocation else 0}
            for key in ALLOCATION_BUCKETS}
    if allocation:
        issues.extend("image allocation exceeded: " + key for key in ALLOCATION_BUCKETS if used[key] > allocation[key])
    limit = min(budget["limit"], definition.get("image_call_limit", budget["limit"]))
    if budget["reserved_calls"] > limit:
        issues.append("task image call total exceeded")
    return {"allocation": rows, "allocation_issues": issues,
            "usable_remaining": min(max(0, limit-budget["reserved_calls"]),
                                    sum(rows[key]["remaining"] for key in LIVE_BUCKETS)) if not issues else 0}


def default_definition(book: dict, task_id: str, *, count: int = 10, characters: list | None = None,
                       styles: list | None = None, image_limit: int = 10) -> dict:
    return {"schema_version": 2, "task_id": task_id, "source_hash": book["source_hash"],
            "canonical_hash": book["canonical_hash"], "target_count": count,
            "character_ids": characters or [], "chapter_ids": [], "quote_types": ["dialogue", "monologue"],
            "style_ids": styles or ["mist_cinema"], "min_distinct_styles": 1, "allow_rewrite": False,
            "min_quote_chars": 12, "max_quote_chars": 100, "candidate_buffer_ratio": 0.3,
            "output": {"width": 1080, "height": 1440, "format": "PNG", "color_space": "sRGB",
                       "cover_policy": "first_card_no_extra_cover"},
            "image_call_limit": image_limit, "image_call_allocation": default_allocation(image_limit), "retry_limit_per_asset": 1,
            "theme": "", "forbidden_elements": [], "input_rights_note": "",
            "allowed_destinations": ["local", "image_gen.imagegen"], "allow_source_upload": False,
            "execution_notes": "应用端协调；未知结果先对账；工具不可用或预算耗尽停相应步骤；无自动社交发布"}


def validate_definition(value: dict, book: dict, characters: dict | None = None):
    expected = set(default_definition(book, "template"))
    if isinstance(value, dict) and type(value.get("schema_version")) is int and value["schema_version"] == 1:
        expected.remove("image_call_allocation")
    if (not isinstance(value, dict) or set(value) != expected or type(value["schema_version"]) is not int
            or value["schema_version"] not in {1, 2}):
        raise ValueError("invalid task contract fields/schema")
    if value["source_hash"] != book["source_hash"] or value["canonical_hash"] != book["canonical_hash"]:
        raise ValueError("task contract targets another source version")
    if any(not isinstance(value[k], str) or not re.fullmatch(r"[0-9a-f]{64}", value[k]) for k in ["source_hash", "canonical_hash"]):
        raise ValueError("invalid task source hashes")
    for name in ["task_id", "input_rights_note", "execution_notes"]:
        if not isinstance(value[name], str) or not value[name].strip():
            raise ValueError("task contract needs " + name)
    for name in ["target_count", "min_quote_chars", "max_quote_chars", "min_distinct_styles"]:
        if type(value[name]) is not int or value[name] <= 0:
            raise ValueError("task contract needs a positive integer " + name)
    if type(value["image_call_limit"]) is not int or value["image_call_limit"] < 0:
        raise ValueError("image call limit must be a nonnegative integer")
    if value["schema_version"] == 2:
        allocation = value["image_call_allocation"]
        if (not isinstance(allocation, dict) or set(allocation) != set(ALLOCATION_BUCKETS)
                or any(type(v) is not int or v < 0 for v in allocation.values())
                or sum(allocation.values()) != value["image_call_limit"]):
            raise ValueError("image allocation must have exact nonnegative integer buckets summing to total limit")
    if value["min_quote_chars"] > value["max_quote_chars"]:
        raise ValueError("invalid quote length range")
    for name in ["character_ids", "chapter_ids", "quote_types", "style_ids", "forbidden_elements", "allowed_destinations"]:
        rows = value[name]
        if not isinstance(rows, list) or any(not isinstance(s, str) or not s.strip() for s in rows) or len(set(rows)) != len(rows):
            raise ValueError("invalid task contract list " + name)
    if not value["character_ids"] or (characters is not None and not set(value["character_ids"]) <= set(characters)):
        raise ValueError("choose explicit existing character IDs; no implicit all-character scope")
    if not value["style_ids"] or not set(value["style_ids"]) <= set(STYLES):
        raise ValueError("unknown/empty task style scope")
    if value["min_distinct_styles"] > min(len(value["style_ids"]), value["target_count"]):
        raise ValueError("required distinct styles exceed task count or allowed styles")
    if not value["quote_types"] or not set(value["quote_types"]) <= {"dialogue", "monologue"}:
        raise ValueError("named card quote types must be dialogue/monologue")
    if not set(value["chapter_ids"]) <= {c["chapter_id"] for c in book["chapters"]}:
        raise ValueError("unknown chapter scope")
    if (type(value["allow_rewrite"]) is not bool or value["allow_source_upload"] is not False
            or not set(value["allowed_destinations"]) <= {"local", "image_gen.imagegen"}
            or "local" not in value["allowed_destinations"] or value["retry_limit_per_asset"] != 1):
        raise ValueError("V1 contract cannot expand transmission, API or retry authority")
    if value["output"] != default_definition(book, "template")["output"]:
        raise ValueError("V1 final export contract supports 1080x1440 PNG sRGB with first-card cover only")
    ratio = value["candidate_buffer_ratio"]
    if type(ratio) not in {int, float} or not math.isfinite(ratio) or not 0 <= ratio <= 1 or not isinstance(value["theme"], str):
        raise ValueError("invalid candidate buffer or theme")


def load_contract(run: Path, book: dict | None = None, *, required: bool = False) -> dict | None:
    path = run / "task-contract.json"
    if not path.exists():
        if required:
            raise ValueError("explicit task contract required; do not infer requested count from existing cards")
        return None
    value = load_json(path)
    if (not isinstance(value, dict) or value.get("schema_version") != 1 or type(value.get("version")) is not int
            or value["version"] < 1 or not isinstance(value.get("definition"), dict)
            or any(not isinstance(value.get(k), str) or not value[k].strip() for k in ["editor", "reason", "recorded_at"])
            or value.get("fingerprint") != fingerprint({"version": value["version"], "definition": value["definition"]})):
        raise ValueError("task contract record damaged")
    validate_definition(value["definition"], book or load_json(run / "book.json"))
    return value


def _base(book: dict, characters: dict, current: dict | None) -> str:
    return fingerprint({"book": book, "characters": characters, "current_contract": current})


def contract_template(run: Path) -> dict:
    recover_documents(run)
    book = load_json(run / "book.json")
    validate_source(run, book)
    characters = load_json(run / "characters.json")
    current = load_contract(run, book)
    with Ledger(run / ".state/ledger.sqlite") as ledger:
        budget = ledger.budget()
    definition = copy.deepcopy(current["definition"]) if current else default_definition(book, run.name, image_limit=budget["limit"])
    if definition["schema_version"] == 1 or (not current and budget["reserved_calls"]):
        usage = allocation_summary(current, budget)["allocation"]
        allocation = {k: row["used"] for k, row in usage.items()}
        allocation["unallocated"] = max(0, definition["image_call_limit"]-budget["reserved_calls"])
        definition.update(schema_version=2, image_call_allocation=allocation)
    return {"schema_version": 1, "expected_fingerprint": _base(book, characters, current),
            "editor": "", "reason": "", "definition": definition,
            "observed_allocation": allocation_summary(current, budget),
            "available_characters": {k: v["name"] for k, v in characters.items()},
            "available_chapters": [{k: c[k] for k in ["chapter_id", "title"]} for c in book["chapters"]]}


def apply_contract(run: Path, request: dict) -> dict:
    recover_documents(run)
    if (request.get("schema_version") != 1 or not isinstance(request.get("editor"), str) or not request["editor"].strip()
            or not isinstance(request.get("reason"), str) or not request["reason"].strip()):
        raise ValueError("contract needs schema, named editor and change reason")
    book = load_json(run / "book.json")
    validate_source(run, book)
    characters = load_json(run / "characters.json")
    current = load_contract(run, book)
    definition = request.get("definition")
    validate_definition(definition, book, characters)
    if current and current["definition"] == definition:
        return {"fingerprint": current["fingerprint"], "version": current["version"], "reused": True, "image_tool_calls": 0}
    if request.get("expected_fingerprint") != _base(book, characters, current):
        raise ValueError("contract template stale; prepare a fresh template")
    if definition["schema_version"] != 2:
        raise ValueError("new contract revisions require explicit image call allocation schema 2")
    with Ledger(run / ".state/ledger.sqlite") as ledger:
        budget = ledger.budget()
    if not budget["reserved_calls"] <= definition["image_call_limit"] <= budget["limit"]:
        raise ValueError("contract limit cannot erase spent calls or expand the existing budget")
    if issues := allocation_summary({"definition": definition}, budget)["allocation_issues"]:
        raise ValueError("; ".join(issues))
    version = current["version"] + 1 if current else 1
    record = {"schema_version": 1, "version": version, "definition": definition,
              "fingerprint": fingerprint({"version": version, "definition": definition}),
              "editor": request["editor"], "reason": request["reason"], "recorded_at": now()}
    request_hash = fingerprint(request)
    history = run / ".state/contract-requests" / (request_hash + ".json")
    if history.exists() and load_json(history) != request:
        raise ValueError("contract request history damaged")
    if not history.exists():
        save_json(history, request)
    def check_budget(ledger):
        latest = ledger.budget()
        if not latest["reserved_calls"] <= definition["image_call_limit"] <= latest["limit"]:
            raise ValueError("budget changed while applying task contract")
        if issues := allocation_summary({"definition": definition}, latest)["allocation_issues"]:
            raise ValueError("budget changed while applying task contract: " + "; ".join(issues))

    transaction = commit_documents(run, {"task-contract.json": record}, reason="task_contract:" + request_hash,
        expected={"book.json": book, "characters.json": characters, "task-contract.json": current}, guard=check_budget)
    return {"fingerprint": record["fingerprint"], "version": version, "transaction": transaction,
            "reused": False, "image_tool_calls": 0}


def selection_issues(contract: dict, cards: list, quotes: dict, *, check_count: bool = True) -> list[str]:
    definition = contract["definition"]
    issues = []
    if check_count and len(cards) != definition["target_count"]:
        issues.append(f"poster count {len(cards)} differs from requested {definition['target_count']}")
    if check_count and len({c["style_id"] for c in cards}) < definition["min_distinct_styles"]:
        issues.append("distinct style count is below task contract requirement")
    for card in cards:
        quote = quotes[card["quote_id"]]
        if card["character_id"] not in definition["character_ids"] or card["style_id"] not in definition["style_ids"]:
            issues.append(card["card_id"] + ": character/style outside contract scope")
        if (quote["quote_type"] not in definition["quote_types"] or
                definition["chapter_ids"] and quote["source"]["chapter_id"] not in definition["chapter_ids"]):
            issues.append(card["card_id"] + ": quote type/chapter outside contract scope")
        if quote["text_mode"] == "rewrite" and not definition["allow_rewrite"]:
            issues.append(card["card_id"] + ": rewrite forbidden by task contract")
        if not definition["min_quote_chars"] <= len(quote["display_text"]) <= definition["max_quote_chars"]:
            issues.append(card["card_id"] + ": display text length outside contract range")
    return issues


def portable_contract(contract: dict | None) -> dict:
    if contract is None:
        return {"state": "missing"}
    return {k: contract[k] for k in ["schema_version", "version", "definition", "fingerprint", "editor", "reason", "recorded_at"]}


def verify_packaged_contract(record: dict, source_hash: str, cards: list, quotes: dict) -> list[str]:
    if record == {"state": "missing"}:
        return ["explicit task contract missing"]
    if (not isinstance(record, dict) or record.get("schema_version") != 1 or type(record.get("version")) is not int
            or record["version"] < 1 or not isinstance(record.get("definition"), dict)
            or record.get("fingerprint") != fingerprint({"version": record["version"], "definition": record["definition"]})):
        raise ValueError("packaged task contract checksum/schema mismatch")
    definition = record["definition"]
    # This verifies internal declarations; original source authenticity still needs the run.
    declared_chapters = definition.get("chapter_ids", [])
    if not isinstance(declared_chapters, list) or any(not isinstance(c, str) for c in declared_chapters):
        raise ValueError("invalid packaged chapter scope")
    chapters = set(declared_chapters) | {q["source"]["chapter_id"] for q in quotes.values()}
    validate_definition(definition, {"source_hash": source_hash, "canonical_hash": definition.get("canonical_hash"),
                                   "chapters": [{"chapter_id": chapter} for chapter in chapters]})
    return selection_issues(record, cards, quotes)
