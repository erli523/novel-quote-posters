"""Exact, ordered source passages for human attribution review; no semantic judgment."""

from bisect import bisect_left, bisect_right
from itertools import pairwise

from .analysis import evidence
from .storage import fingerprint

CONTEXT_POLICY_VERSION = 1


class SourceContext:
    def __init__(self, book: dict):
        self.book = book
        self.paragraphs = book["paragraphs"]
        self.starts = [p["start"] for p in self.paragraphs]
        self.ends = [p["end"] for p in self.paragraphs]
        self.chapters = {c["chapter_id"]: c for c in book["chapters"]}
        self.chapter_ranges = {}
        for index, paragraph in enumerate(self.paragraphs):
            span = self.chapter_ranges.setdefault(paragraph["chapter_id"], [index, index])
            span[1] = index

    def target(self, quote: dict) -> dict:
        seeds = [(quote["source"], "quote", None)]
        for index, item in enumerate(quote["speaker"].get("evidence", [])):
            evidence(self.book, item)
            seeds.append((item, "speaker_evidence", index))
        groups = []
        for item, role, index in seeds:
            first = bisect_right(self.ends, item["start"])
            last = bisect_left(self.starts, item["end"])-1
            if first > last:
                raise ValueError("review source span has no indexed paragraph")
            cursor = first
            while cursor <= last:
                chapter_id = self.paragraphs[cursor]["chapter_id"]
                low, high = self.chapter_ranges[chapter_id]
                segment_end = min(last, high)
                groups.append({"first": max(low, cursor-1), "last": min(high, segment_end+1),
                               "chapter_id": chapter_id, "roles": {role},
                               "evidence_indexes": {index} if index is not None else set()})
                cursor = segment_end+1
        merged = []
        for group in sorted(groups, key=lambda row: row["first"]):
            if merged and merged[-1]["chapter_id"] == group["chapter_id"] and group["first"] <= merged[-1]["last"]+1:
                merged[-1]["last"] = max(merged[-1]["last"], group["last"])
                merged[-1]["roles"].update(group["roles"])
                merged[-1]["evidence_indexes"].update(group["evidence_indexes"])
            else:
                merged.append(group)
        passages = []
        for group in merged:
            paragraphs = self.paragraphs[group["first"]:group["last"]+1]
            start, end = paragraphs[0]["start"], paragraphs[-1]["end"]
            passages.append({"chapter_id": group["chapter_id"], "chapter": self.chapters[group["chapter_id"]]["title"],
                             "start": start, "end": end, "text": self.book["text"][start:end],
                             "paragraph_ids": [p["paragraph_id"] for p in paragraphs],
                             "roles": sorted(group["roles"]), "evidence_indexes": sorted(group["evidence_indexes"])})
        material = {"policy_version": CONTEXT_POLICY_VERSION, "canonical_hash": self.book["canonical_hash"],
                    "coordinate_unit": "unicode_codepoint", "interval": "half_open", "passages": passages,
                    "omitted_ranges": [{"start": left["end"], "end": right["start"],
                                        "characters": right["start"]-left["end"]}
                                       for left, right in pairwise(passages) if left["end"] < right["start"]],
                    "speaker_explanation": quote["speaker"].get("explanation", "")}
        return {**material, "fingerprint": fingerprint(material)}


def context_review_complete(review: dict, context_hash: str) -> bool:
    return (review.get("context_policy_version") == CONTEXT_POLICY_VERSION
            and review.get("context_fingerprint") == context_hash)
