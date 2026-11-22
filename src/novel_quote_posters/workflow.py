"""Transactional dependency ledger and bounded application-tool call reservations."""

import json
import sqlite3
from pathlib import Path

from .storage import digest_file, now


class Ledger:
    def __init__(self, path: Path, limit: int = 10):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA foreign_keys=ON")
        migration_from = None
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='config'").fetchone():
            previous_version = self.db.execute("SELECT value FROM config WHERE key='schema_version'").fetchone()
            if previous_version and previous_version[0] not in {"1", "2"}:
                self.db.close()
                raise ValueError("unsupported future ledger schema; refusing downgrade")
            if not previous_version or previous_version[0] == "1":
                migration_from = previous_version[0] if previous_version else "legacy"
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY, path TEXT, hash TEXT, valid INT);
          CREATE TABLE IF NOT EXISTS dependencies(child TEXT, parent TEXT, PRIMARY KEY(child,parent));
          CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY, at TEXT, type TEXT, payload TEXT);
          CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY, input_hash TEXT, status TEXT, output TEXT);
          CREATE TABLE IF NOT EXISTS call_requests(id TEXT PRIMARY KEY, payload TEXT);
          CREATE TABLE IF NOT EXISTS call_results(id TEXT PRIMARY KEY, payload TEXT);
          CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY, value TEXT);
        """)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO config VALUES ('limit',?)", (str(limit),))
            self.db.execute("INSERT OR REPLACE INTO config VALUES ('schema_version','2')")
            if migration_from:
                self._event("ledger_migrated", {"from": migration_from, "to": "2",
                    "changes": "additive request/result tables; core calls, assets and events retained",
                    "rollback": "prior core schema remains readable; no core columns removed"})

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _event(self, kind: str, payload: dict):
        self.db.execute("INSERT INTO events(at,type,payload) VALUES (?,?,?)",
                        (now(), kind, json.dumps(payload, ensure_ascii=False)))

    def _invalidate(self, identity: str) -> list[str]:
        rows = self.db.execute("""WITH RECURSIVE affected(id) AS (
          SELECT ? UNION SELECT child FROM dependencies JOIN affected ON parent=affected.id
        ) SELECT id FROM affected""", (identity,)).fetchall()
        ids = sorted(row[0] for row in rows)
        self.db.executemany("UPDATE assets SET valid=0 WHERE id=?", [(i,) for i in ids])
        self._event("invalidated", {"root": identity, "affected": ids})
        return ids

    def invalidate(self, identity: str) -> list[str]:
        with self.db:
            return self._invalidate(identity)

    def put(self, identity: str, path: Path, parents: list[str] = ()):
        path = path.resolve()
        hash_value = digest_file(path)
        with self.db:
            old = self.db.execute("SELECT hash FROM assets WHERE id=?", (identity,)).fetchone()
            if old and old[0] != hash_value:
                self._invalidate(identity)
            for parent in parents:
                if parent == identity or not self.valid(parent):
                    raise ValueError("missing/invalid dependency or self-cycle")
                # Reject transitive cycles, including updates of an existing graph node.
                descendants = self.db.execute("""WITH RECURSIVE d(id) AS (
                  SELECT ? UNION SELECT child FROM dependencies JOIN d ON parent=d.id
                ) SELECT id FROM d""", (identity,)).fetchall()
                if parent in {row[0] for row in descendants}:
                    raise ValueError("dependency cycle")
            self.db.execute("INSERT OR REPLACE INTO assets VALUES (?,?,?,1)",
                            (identity, str(path), hash_value))
            self.db.execute("DELETE FROM dependencies WHERE child=?", (identity,))
            self.db.executemany("INSERT INTO dependencies VALUES (?,?)",
                                [(identity, p) for p in parents])
            self._event("asset_committed", {"id": identity, "hash": hash_value, "parents": parents})

    def valid(self, identity: str, seen=None) -> bool:
        seen = set() if seen is None else seen
        if identity in seen:
            return False
        seen = seen | {identity}
        row = self.db.execute("SELECT path,hash,valid FROM assets WHERE id=?", (identity,)).fetchone()
        if not row or not row[2] or not Path(row[0]).is_file():
            return False
        if digest_file(Path(row[0])) != row[1]:
            return False
        return all(self.valid(parent, seen) for (parent,) in self.db.execute(
            "SELECT parent FROM dependencies WHERE child=?", (identity,)).fetchall())

    def reserve(self, call_id: str, input_hash: str, request: dict | None = None) -> dict:
        """Reserve before external side effects; existing unknown calls are never retried."""
        with self.db:
            self.db.execute("UPDATE config SET value=value WHERE key='limit'")  # writer lock
            row = self.db.execute("SELECT input_hash,status,output FROM calls WHERE id=?",
                                  (call_id,)).fetchone()
            if row:
                if row[0] != input_hash:
                    raise ValueError("call ID reused with changed inputs")
                reusable = row[1] == "succeeded" and row[2] and self.valid(row[2])
                return {"action": "reuse" if reusable else "reconcile",
                        "status": row[1], "output": row[2]}
            count = self.db.execute("SELECT count(*) FROM calls").fetchone()[0]
            limit = int(self.db.execute("SELECT value FROM config WHERE key='limit'").fetchone()[0])
            if count >= limit:
                raise ValueError("image call budget exhausted")
            self.db.execute("INSERT INTO calls VALUES (?,?,'reserved',NULL)", (call_id, input_hash))
            if request is not None:
                self.db.execute("INSERT INTO call_requests VALUES (?,?)",
                                (call_id, json.dumps(request, ensure_ascii=False, sort_keys=True)))
            self._event("call_reserved", {"id": call_id, "input_hash": input_hash})
            return {"action": "invoke", "status": "reserved", "output": None}

    def resolve_call(self, call_id: str, status: str, output: str | None = None,
                     details: dict | None = None):
        if status not in {"succeeded", "failed", "unknown_outcome", "tool_unavailable"}:
            raise ValueError("invalid tool outcome")
        if status == "succeeded" and (not output or not self.valid(output)):
            raise ValueError("successful call must reference a committed valid asset")
        with self.db:
            self.db.execute("UPDATE config SET value=value WHERE key='limit'")
            previous = self.db.execute("SELECT status,output FROM calls WHERE id=?", (call_id,)).fetchone()
            if not previous:
                raise ValueError("unreserved call")
            previous_details = self.db.execute("SELECT payload FROM call_results WHERE id=?", (call_id,)).fetchone()
            if previous[0] in {"succeeded", "failed", "tool_unavailable"}:
                old_details = json.loads(previous_details[0]) if previous_details else None
                if (previous[0], previous[1], old_details) == (status, output, details):
                    return
                raise ValueError("terminal tool outcome cannot be rewritten; use a new call ID")
            self.db.execute("UPDATE calls SET status=?,output=? WHERE id=?", (status, output, call_id))
            if details is not None:
                self.db.execute("INSERT OR REPLACE INTO call_results VALUES (?,?)",
                                (call_id, json.dumps(details, ensure_ascii=False, sort_keys=True)))
            self._event("call_resolved", {"id": call_id, "status": status, "output": output})

    def budget(self) -> dict:
        limit = int(self.db.execute("SELECT value FROM config WHERE key='limit'").fetchone()[0])
        rows = self.db.execute("""SELECT c.id,c.input_hash,c.status,c.output,q.payload,r.payload
          FROM calls c LEFT JOIN call_requests q ON q.id=c.id LEFT JOIN call_results r ON r.id=c.id
          ORDER BY c.rowid""").fetchall()
        calls = [{"call_id": identity, "input_hash": value, "status": status, "output": output,
                  "request": json.loads(request) if request else None,
                  "result": json.loads(result) if result else None}
                 for identity, value, status, output, request, result in rows]
        return {"limit": limit, "reserved_calls": len(calls), "remaining": max(0, limit-len(calls)),
                "succeeded": sum(c["status"] == "succeeded" for c in calls),
                "needs_reconciliation": sum(c["status"] in {"reserved", "unknown_outcome"} for c in calls),
                "calls": calls}

    def events(self) -> list[dict]:
        return [{"seq": seq, "at": at, "type": kind, "payload": json.loads(payload)}
                for seq, at, kind, payload in self.db.execute("SELECT * FROM events ORDER BY seq")]
