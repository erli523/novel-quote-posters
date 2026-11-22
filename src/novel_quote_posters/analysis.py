"""Validate application-side semantic analysis without granting it human approval."""

from .quotes import make_quote


def evidence(book: dict, item: dict) -> dict:
    start, end = item["start"], item["end"]
    if not (0 <= start < end <= len(book["text"])) or book["text"][start:end] != item["text"]:
        raise ValueError("semantic evidence does not match source")
    return dict(item)


def apply_analysis(book: dict, windows: list[dict], proposal: dict) -> tuple[list, dict, list]:
    if proposal["source_hash"] != book["source_hash"]:
        raise ValueError("semantic analysis targets a different source")
    known_windows = {w["chunk_id"]: w for w in windows}
    processed = set()
    for row in proposal["processed_chunks"]:
        original = known_windows[row["chunk_id"]]
        if row["sha256"] != original["sha256"] or not row.get("summary"):
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
        for item in speaker.get("evidence", []):
            evidence(book, item)
        if row["quote_type"] not in {"dialogue", "monologue", "narration"}:
            raise ValueError("unknown quote type")
        if speaker["status"] in {"ambiguous", "narration"} and speaker["character_id"] is not None:
            raise ValueError("ambiguous/narration must not carry named attribution")
        quote.update(speaker=speaker, quote_type=row["quote_type"],
                     selection={**row.get("selection", {}), "origin": "application_assistant",
                                "quality_review": "pending"},
                     review={"status": "pending", "reviewer_type": None})
        quotes.append(quote)
    updated = [{**w, "semantic_status": "application_processed" if w["chunk_id"] in processed
                else "pending"} for w in windows]
    return quotes, characters, updated
