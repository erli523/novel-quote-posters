"""Versioned human visual-review scope; deterministic checks cannot supply judgments."""

VISUAL_POLICY_VERSION = 2
VISUAL_CHECKS = {
    "character_consistency": "人物：对照档案、基准图和同组卡片，脸型、发型、年龄及服装时代一致",
    "text_exact": "文字：逐字核对引文、人物名、作品名及改写标签，无乱码和伪文字",
    "mobile_readability": "手机：在 360×480 预览中能够清楚阅读，文字未遮挡人物面部",
    "crop_safety": "裁切：查看完整图与模拟裁切，关键信息安全，实际发布仍核对平台预览",
    "style_scene": "画面：单人主体、风格和语句情境合适，无明显错误元素",
}


def visual_review_complete(review: dict) -> bool:
    return (review.get("visual_policy_version") == VISUAL_POLICY_VERSION
            and review.get("visual_checks") == {key: "passed" for key in VISUAL_CHECKS})
