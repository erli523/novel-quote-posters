"""Validate application-side semantic analysis without granting it human approval."""

import copy
import json
from pathlib import Path

from .documents import commit_documents, recover_documents
from .quotes import make_quote
from .source import validate_source
from .storage import digest_file, load_json, save_bytes, sha256


def evidence(book: dict, item: dict) -> dict:
    start, end = item["start"], item["end"]
    if not (0 <= start < end <= len(book["text"])) or book["text"][start:end] != item["text"]:
        raise ValueError("semantic evidence does not match source")
    return dict(item)


def apply_analysis(book: dict, windows: list[dict], proposal: dict) -> tuple[list, dict, list]:
    if proposal["source_hash"] != book["source_hash"]:
        raise ValueError("semantic analysis targets a different source")
    if sha256(book["text"].encode()) != book["canonical_hash"]:
        raise ValueError("canonical source changed")
    known_windows = {w["chunk_id"]: w for w in windows}
    processed = set()
    for row in proposal["processed_chunks"]:
        original = known_windows[row["chunk_id"]]
        actual = sha256(book["text"][original["start"]:original["end"]].encode())
        if row["sha256"] != original["sha256"] or actual != original["sha256"] or not row.get("summary"):
            raise ValueError("chunk hash/summary missing or stale")
        processed.add(row["chunk_id"])
    characters = proposal["characters"]
    for character_id, character in characters.items():
        if character["character_id"] != character_id or not character.get("name"):
            raise ValueError("invalid character identity")
        for trait in character.get("traits", []):
            if trait["kind"] not in {"explicit", "inferred", "user_specified", "design_choice"}:
                raise ValueError("invalid trait evidence category")
            if trait["kind"] in {"explicit", "inferred"} and not trait.get("evidence"):
                raise ValueError("source-based trait requires evidence")
            for item in trait.get("evidence", []):
                evidence(book, item)
    quotes, seen = [], set()
    for row in proposal["quotes"]:
        quote = make_quote(book, row["quote_exact"], row["start"])
        if quote["quote_id"] in seen:
            continue
        seen.add(quote["quote_id"])
        speaker = row["speaker"]
        if speaker["status"] not in {"explicit", "context_inferred", "ambiguous", "narration"}:
            raise ValueError("invalid speaker status")
        if speaker["character_id"] and speaker["character_id"] not in characters:
            raise ValueError("unknown speaker")
        if speaker["status"] in {"explicit", "context_inferred"} and not speaker.get("evidence"):
            raise ValueError("named attribution needs evidence")
        if speaker["status"] in {"explicit", "context_inferred"} and not speaker["character_id"]:
            raise ValueError("named attribution needs a character")
        for item in speaker.get("evidence", []):
            evidence(book, item)
        if row["quote_type"] not in {"dialogue", "monologue", "narration"}:
            raise ValueError("unknown quote type")
        if (row["quote_type"] == "narration") != (speaker["status"] == "narration"):
            raise ValueError("narration must be classified consistently and never named dialogue")
        if speaker["status"] in {"ambiguous", "narration"} and speaker["character_id"] is not None:
            raise ValueError("ambiguous/narration must not carry named attribution")
        quote.update(speaker=speaker, quote_type=row["quote_type"],
                     selection={**row.get("selection", {}), "origin": "application_assistant",
                                "quality_review": "pending"},
                     review={"status": "pending", "reviewer_type": None})
        quotes.append(quote)
    updated = [{**w, "semantic_status": "application_processed" if w["chunk_id"] in processed
                else w.get("semantic_status", "pending")} for w in windows]
    return quotes, characters, updated


def apply_analysis_file(run: Path, path: Path) -> dict:
    """Merge a fixed proposal snapshot, preserving explicit display edits and references."""
    recover_documents(run)
    names = ["book.json", "chunks.json", "quotes.json", "characters.json", "analysis-report.json"]
    before = {name: load_json(run / name) if (run / name).exists() else None for name in names}
    if any(before[name] is None for name in ["book.json", "chunks.json", "quotes.json"]):
        raise ValueError("an indexed run is required before applying semantic analysis")
    book = before["book.json"]
    validate_source(run, book)
    raw = path.read_bytes()
    proposal_hash = sha256(raw)
    proposal = json.loads(raw.decode("utf-8"))
    quotes, proposed_characters, updated = apply_analysis(book, before["chunks.json"], proposal)
    snapshot = run / "analysis" / (proposal_hash + ".json")
    if snapshot.exists() and digest_file(snapshot) != proposal_hash:
        raise ValueError("analysis snapshot checksum mismatch")
    if not snapshot.exists():
        save_bytes(snapshot, raw)
    existing = {q["quote_id"]: copy.deepcopy(q) for q in before["quotes.json"]}
    for quote in quotes:
        previous = existing.get(quote["quote_id"])
        if previous and previous["source"] == quote["source"] and previous["quote_exact"] == quote["quote_exact"]:
            for key in ["display_text", "text_mode"]:
                quote[key] = previous[key]
        if previous and {k: v for k, v in previous.items() if k != "review"} == {
                k: v for k, v in quote.items() if k != "review"}:
            quote["review"] = previous["review"]
        existing[quote["quote_id"]] = quote
    characters = copy.deepcopy(before["characters.json"] or {})
    for identity, value in proposed_characters.items():
        character = copy.deepcopy(value)
        previous = characters.get(identity, {})
        for key in ["reference_image", "reference_asset_id", "visual_anchor_version"]:
            if key in previous:
                if key != "visual_anchor_version" and character.get(key) and character[key] != previous[key]:
                    raise ValueError("reference images are managed by set-reference, not semantic proposals")
                character[key] = previous[key]
            elif key != "visual_anchor_version" and character.get(key):
                raise ValueError("new references require actual image registration and set-reference")
        characters[identity] = character
    report = {"source_hash": book["source_hash"], "proposal_hash": proposal_hash,
              "semantic_chunks_processed": sum(w["semantic_status"] == "application_processed" for w in updated),
              "chunks_total": len(updated), "quotes": len(quotes),
              "human_review": "pending", "reviewer_type": "application_assistant"}
    proposed = {"quotes.json": list(existing.values()), "characters.json": characters,
                "chunks.json": updated, "analysis-report.json": report}
    changes = {name: value for name, value in proposed.items() if value != before[name]}
    validate_source(run, book)
    transaction = commit_documents(run, changes, reason="application_analysis", expected=before) if changes else None
    return {**report, "transaction": transaction, "reused": not changes}
