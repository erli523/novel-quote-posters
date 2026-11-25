"""Shared deterministic layout validation; no imagery or human judgments."""

import math

from PIL import ImageColor

LAYOUT_KEYS = ("bg", "fg", "accent", "image", "panel", "text",
               "image_fit", "max_crop", "image_anchor", "show_chapter")


def _number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def normalize_layout_overrides(value: dict | None) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value)-set(LAYOUT_KEYS):
        raise ValueError("layout overrides contain unsupported fields")
    result = {}
    for key, item in value.items():
        if key == "show_chapter":
            if type(item) is not bool:
                raise ValueError("show_chapter must be a boolean")
            result[key] = item
        elif key in {"bg", "fg", "accent"}:
            if not isinstance(item, str):
                raise ValueError("layout color must be an opaque RGB string")
            try:
                rgb = ImageColor.getrgb(item)
            except (ValueError, TypeError) as error:
                raise ValueError("layout color must be an opaque RGB string") from error
            if len(rgb) != 3:
                raise ValueError("layout color must be opaque RGB")
            result[key] = "#" + "".join(f"{channel:02x}" for channel in rgb)
        elif key in {"image", "panel", "text"}:
            if (not isinstance(item, (list, tuple)) or len(item) != 4
                    or any(not _number(v) or not 0 <= v <= 1 for v in item)
                    or item[0] >= item[2] or item[1] >= item[3]):
                raise ValueError("layout rectangle must be ordered normalized coordinates")
            result[key] = list(item)
        elif key == "image_fit":
            if not isinstance(item, str) or item not in {"auto", "cover", "contain"}:
                raise ValueError("unknown image fit mode")
            result[key] = item
        elif key == "max_crop":
            if not _number(item) or not 0 <= item < 1:
                raise ValueError("image max_crop must be between zero and one")
            result[key] = item
        else:
            if (not isinstance(item, (list, tuple)) or len(item) != 2
                    or any(not _number(v) or not 0 <= v <= 1 for v in item)):
                raise ValueError("image anchor must contain two normalized coordinates")
            result[key] = list(item)
    return result


def validate_effective_layout(style: dict) -> None:
    text, panel = style["text"], style["panel"]
    if not (text[0] >= 80/1080 and text[2] <= 1-80/1080
            and text[1] >= .15 and text[3] <= .875):
        raise ValueError("layout text must remain inside safe content area")
    if not (panel[0] <= text[0] < text[2] <= panel[2]
            and panel[1] <= text[1] < text[3] <= panel[3]):
        raise ValueError("layout text must remain inside opaque text panel")

    def luminance(color):
        values = [channel/255 for channel in ImageColor.getrgb(color)]
        values = [v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in values]
        return sum(a*b for a, b in zip(values, [.2126, .7152, .0722]))

    low, high = sorted([luminance(style["bg"]), luminance(style["fg"])])
    if (high+.05)/(low+.05) < 4.5:
        raise ValueError("layout text-panel contrast is below 4.5:1")
