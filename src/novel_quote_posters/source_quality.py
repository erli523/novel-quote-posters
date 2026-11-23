"""Read-only source observations, not transcription or semantic certification."""

from collections import Counter

from .quotes import candidates


def source_observations(book: dict) -> dict:
    pairs, issues = candidates(book)
    square = book["text"].count("□")
    replacement = book["text"].count("\ufffd")
    warnings = []
    if square or replacement:
        warnings.append("Glyph markers observed; inspect the retained source without inventing missing text.")
    if issues:
        warnings.append("Unbalanced quote delimiters can suppress candidates; review source windows and original punctuation.")
    return {"checker_version": "source-observations-1", "source_hash": book["source_hash"],
            "canonical_hash": book["canonical_hash"], "square_glyphs": square,
            "replacement_characters": replacement, "balanced_outer_pairs_observed": len(pairs),
            "quote_delimiter_issues": dict(Counter(row["type"] for row in issues)),
            "quote_delimiter_issue_count": len(issues), "warnings": warnings,
            "observation_scope": "current canonical text; mechanical outer quote pairs only",
            "transcription_accuracy": "not_verified", "candidate_recall": "not_measured",
            "source_modified": False}
