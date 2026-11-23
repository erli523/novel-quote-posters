"""Content-bound whole-album review; ordered images do not imply human approval."""

from pathlib import Path

from .storage import digest_file, fingerprint, load_json

ALBUM_POLICY_VERSION = 1
ALBUM_CHECKS = {
    "order_complete": "数量与顺序：卡片无重复、缺失，上传顺序符合本次任务",
    "opening_page": "首图：首张卡片清楚表达主题，标题与原句区分，无额外封面凑数",
    "reading_flow": "阅读节奏：查看首、中、末页，语句衔接与收尾合适",
    "group_consistency": "整组一致性：人物、时代、风格和文字层级适合放在同一图集",
}


def album_target(book: dict, rows: list[dict]) -> dict | None:
    if not rows:
        return None
    if len({row["card_id"] for row in rows}) != len(rows):
        raise ValueError("duplicate album card IDs")
    material = {"policy_version": ALBUM_POLICY_VERSION, "source_hash": book["source_hash"],
                "book_title": book["title"], "cover_policy": "first_card_no_extra_cover",
                "count": len(rows), "ordered_cards": [
                    {"sequence": index, **{k: row[k] for k in ["card_id", "quote_id", "fingerprint", "image_sha256", "draft"]}}
                    for index, row in enumerate(rows, 1)]}
    return {"material": material, "fingerprint": fingerprint(material), "status": "pending",
            "checks": dict.fromkeys(ALBUM_CHECKS, "pending"), "note": ""}


def selection_target(book: dict, selected: list, draft: bool) -> dict:
    return album_target(book, [{"card_id": card["card_id"], "quote_id": card["quote_id"],
                               "fingerprint": approval, "image_sha256": digest_file(image), "draft": draft}
                              for card, quote, image, layout, approval in selected])


def load_album_review(run: Path) -> dict | None:
    path = run / "album-review.json"
    if not path.exists():
        return None
    value = load_json(path)
    if not isinstance(value, dict):
        raise ValueError("invalid whole-album review document")  # noqa: TRY004
    return value


def album_review_complete(review: dict | None, target: dict) -> bool:
    return bool(isinstance(review, dict) and review.get("schema_version") == 1 and review.get("status") == "approved"
                and review.get("reviewer_type") == "human" and review.get("reviewer")
                and review.get("attested") is True and review.get("reviewed_at")
                and review.get("fingerprint") == target["fingerprint"]
                and review.get("material") == target["material"]
                and review.get("checks") == dict.fromkeys(ALBUM_CHECKS, "passed"))


def album_status(run: Path, target: dict | None) -> dict:
    if target is None:
        return {"state": "awaiting_images_or_render"}
    review = load_album_review(run)
    state = ("approved" if album_review_complete(review, target) else "pending" if not review
             else "invalidated" if review.get("fingerprint") != target["fingerprint"] else "rejected"
             if review.get("status") == "rejected" else "pending")
    return {"state": state, "fingerprint": target["fingerprint"], "count": target["material"]["count"]}


def portable_album_review(run: Path, target: dict, draft: bool) -> dict:
    review = load_album_review(run)
    if not draft and album_review_complete(review, target):
        return {k: review[k] for k in ["schema_version", "material", "fingerprint", "status", "checks",
                                      "reviewer_type", "reviewer", "attested", "reviewed_at", "note"]}
    return {"schema_version": 1, "material": target["material"], "fingerprint": target["fingerprint"],
            "status": "pending", "checks": dict.fromkeys(ALBUM_CHECKS, "pending"),
            "note": "待审交付；不继承其他版本的整组批准"}
