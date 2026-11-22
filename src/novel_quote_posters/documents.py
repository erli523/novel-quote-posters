"""Recoverable multi-document updates for one run; immutable transaction history.

An fsynced journal pointer is the commit point. Readers recover pending writes before
using the JSON files. SQLite's writer lock serializes cooperating CLI operations and
is released by the OS on process death; no stale lock file needs to be guessed away.
"""

import uuid
from pathlib import Path

from .storage import contained, digest_file, load_json, now, save_json
from .workflow import Ledger

ALLOWED = {"quotes.json", "characters.json", "cards.json", "chunks.json", "analysis-report.json"}


def _recover(run: Path, ledger: Ledger) -> bool:
    pointer = run / ".state" / "pending-documents.json"
    head = run / ".state" / "documents-head.json"
    if not pointer.exists():
        # Files and the durable head can survive a crash just before SQLite commits.
        # Reconstruct that event without undoing later, deliberate JSON edits.
        if head.exists():
            reference = load_json(head)
            recorded = ledger.db.execute("SELECT value FROM config WHERE key='documents_event_head'").fetchone()
            if not recorded or recorded[0] != reference["path"]:
                transaction = contained(run, reference["path"])
                if digest_file(transaction) != reference["sha256"]:
                    raise ValueError("document head checksum mismatch")
                ledger._event("documents_materialized", {"transaction": reference["path"],
                                                        "reason": "reconciled_durable_head"})
                ledger.db.execute("INSERT OR REPLACE INTO config VALUES ('documents_event_head',?)",
                                  (reference["path"],))
                return True
        return False
    reference = load_json(pointer)
    transaction = contained(run, reference["path"])
    if digest_file(transaction) != reference["sha256"]:
        raise ValueError("document transaction checksum mismatch")
    payload = load_json(transaction)
    if not set(payload["after"]) <= ALLOWED:
        raise ValueError("unsupported transaction document")
    for name, value in payload["after"].items():
        save_json(run / name, value)
    ledger._event("documents_materialized", {"transaction": reference["path"],
                                            "reason": payload["reason"]})
    ledger.db.execute("INSERT OR REPLACE INTO config VALUES ('documents_event_head',?)",
                      (reference["path"],))
    save_json(head, reference)
    pointer.unlink()
    return True


def recover_documents(run: Path) -> bool:
    if not run.is_dir():
        raise ValueError("run directory does not exist")
    with Ledger(run / ".state" / "ledger.sqlite") as ledger, ledger.db:
        ledger.db.execute("UPDATE config SET value=value WHERE key='limit'")
        return _recover(run, ledger)


def commit_documents(run: Path, values: dict, *, reason: str) -> str:
    if not values or not set(values) <= ALLOWED:
        raise ValueError("unsupported transaction document")
    if not run.is_dir():
        raise ValueError("run directory does not exist")
    with Ledger(run / ".state" / "ledger.sqlite") as ledger, ledger.db:
        ledger.db.execute("UPDATE config SET value=value WHERE key='limit'")
        _recover(run, ledger)
        relative = f".state/transactions/{uuid.uuid4().hex}.json"
        transaction = run / relative
        before = {name: load_json(run / name) if (run / name).exists() else None for name in values}
        save_json(transaction, {"schema_version": 1, "at": now(), "reason": reason,
                                "before": before, "after": values})
        save_json(run / ".state" / "pending-documents.json",
                  {"path": relative, "sha256": digest_file(transaction)})
        _recover(run, ledger)
        return relative
