"""Persistent per-target human-decision versions, independent of render invalidation."""

import copy
import json
import re
from pathlib import Path

from .storage import contained, fingerprint, load_json
from .workflow import Ledger

DOCUMENT = "review-decisions.json"
EMPTY = {"schema_version": 1, "quotes": {}, "cards": {}, "album": 0}


def load_decisions(run: Path) -> tuple[dict | None, dict]:
    path = run / DOCUMENT
    raw = load_json(path) if path.exists() else None
    # A deleted or rolled-back version file must not silently restart decisions at zero.
    with Ledger(run / ".state/ledger.sqlite") as ledger:
        for (payload,) in ledger.db.execute(
            "SELECT payload FROM events WHERE type='documents_materialized' ORDER BY seq DESC"
        ):
            transaction = load_json(contained(run, json.loads(payload)["transaction"]))
            if DOCUMENT in transaction["after"]:
                if raw != transaction["after"][DOCUMENT]:
                    raise ValueError(
                        "review decision history missing or changed; preserve evidence and recover"
                    )
                break
    value = copy.deepcopy(raw if raw is not None else EMPTY)
    if (
        not isinstance(value, dict)
        or set(value) != set(EMPTY)
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or type(value["album"]) is not int
        or value["album"] < 0
        or any(
            not isinstance(value[key], dict)
            or any(
                not isinstance(k, str) or type(v) is not int or v < 0 for k, v in value[key].items()
            )
            for key in ["quotes", "cards"]
        )
    ):
        raise ValueError("invalid persistent human decision versions")
    return raw, value


def decision_target(versions: dict, category: str, identity: str | None, review) -> dict:
    revision = versions[category].get(identity, 0) if category != "album" else versions["album"]
    return {"decision_revision": revision, "prior_review_fingerprint": fingerprint(review)}


def advance_decision(versions: dict, category: str, identity: str | None, review, row: dict):
    expected = decision_target(versions, category, identity, review)
    if type(row.get("decision_revision")) is not int or any(
        row.get(k) != v for k, v in expected.items()
    ):
        raise ValueError(
            "later human decision or stale review snapshot; generate a fresh review page"
        )
    if category == "album":
        versions["album"] += 1
    else:
        versions[category][identity] = expected["decision_revision"] + 1


def validate_decision_fields(row: dict):
    previous = row.get("prior_review_fingerprint")
    if (
        type(row.get("decision_revision")) is not int
        or row["decision_revision"] < 0
        or not isinstance(previous, str)
        or not re.fullmatch("[0-9a-f]{64}", previous)
    ):
        raise ValueError(
            "later human decision or stale review snapshot; generate a fresh review page"
        )
