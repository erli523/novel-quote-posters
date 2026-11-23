"""Deterministic Chinese layout, independent text layers and honest draft previews."""

import math
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import Image, ImageCms, ImageDraw, ImageFont, ImageOps

from .storage import digest_file, save_json

# Normalized boxes are scaled separately for each export size, never stretched bitmaps.
STYLES = {
    "mist_cinema": {"bg": "#080f18", "fg": "#f5f0e5", "accent": "#d7b576",
                        "image": (.06, .06, .94, .67), "panel": (0, .67, 1, 1),
                        "text": (.09, .70, .91, .86), "image_fit": "cover", "image_anchor": (.5, .05)},
    "archive_paper": {"bg": "#eee4d0", "fg": "#312d29", "accent": "#86684c",
                          "image": (.08, .06, .92, .56), "panel": (0, .56, 1, 1), "text": (.09, .63, .91, .84)},
    "dark_stage": {"bg": "#0c0b10", "fg": "#fff2e1", "accent": "#d97962",
                       "image": (.24, .06, .76, .61), "panel": (0, .61, 1, 1), "text": (.15, .64, .85, .84)},
    "ink_whitespace": {"bg": "#f4f0e4", "fg": "#263b38", "accent": "#9a493c",
                           "image": (.28, .39, .94, .87), "panel": (0, 0, 1, .38), "text": (.09, .17, .91, .37)},
    "modern_editorial": {"bg": "#f2eedf", "fg": "#181a1d", "accent": "#bd442b",
                             "image": (.08, .43, .92, .89), "panel": (0, 0, 1, .42), "text": (.08, .16, .92, .40)},
    "warm_memory": {"bg": "#e9d8bf", "fg": "#392e29", "accent": "#8a5a3b",
                        "image": (0, 0, 1, .64), "panel": (.065, .54, .935, .94), "text": (.1, .6, .9, .81)},
}
CLOSING = set("，。！？；：、）》」』】〉…,.!?;:%")
OPENING = set("（《「『【〈(")


def fit_artwork(source: Image.Image, bounds: tuple, *, mode: str = "auto",
                max_crop: float = .22, anchor: tuple = (.5, .25)) -> tuple:
    """Preserve aspect ratio and disclose cropping; never infer where a face is."""
    if not isinstance(mode, str) or mode not in {"auto", "cover", "contain"}:
        raise ValueError("unknown image fit mode")
    if (not isinstance(max_crop, (int, float)) or isinstance(max_crop, bool)
            or not math.isfinite(max_crop) or not 0 <= max_crop < 1):
        raise ValueError("image max_crop must be between zero and one")
    if (not isinstance(anchor, (tuple, list)) or len(anchor) != 2
            or any(not isinstance(v, (int, float)) or isinstance(v, bool)
                   or not math.isfinite(v) or not 0 <= v <= 1 for v in anchor)):
        raise ValueError("image anchor must contain two normalized coordinates")
    left, top, right, bottom = bounds
    width, height = right-left, bottom-top
    if width <= 0 or height <= 0:
        raise ValueError("image frame must have positive dimensions")
    sw, sh = source.size
    scale = max(width/sw, height/sh)
    retained = width*height/(sw*sh*scale*scale)
    applied = "contain" if mode == "auto" and 1-retained > max_crop else (
        "cover" if mode == "auto" else mode)
    if applied == "contain":
        picture = ImageOps.contain(source, (width, height), Image.Resampling.LANCZOS)
        x, y = left+(width-picture.width)//2, top+(height-picture.height)//2
        crop, retained = [0, 0, sw, sh], 1.0
    else:
        cw, ch = width/scale, height/scale
        x0, y0 = (sw-cw)*anchor[0], (sh-ch)*anchor[1]
        crop = [x0, y0, x0+cw, y0+ch]
        picture = ImageOps.fit(source, (width, height), Image.Resampling.LANCZOS,
                               centering=tuple(anchor))
        x, y = left, top
    return picture, (x, y), {"requested": mode, "applied": applied, "source_size": [sw, sh],
        "frame_box": list(bounds), "paste_box": [x, y, x+picture.width, y+picture.height],
        "source_crop_box": crop, "retained_source_area": retained,
        "max_crop": max_crop, "crop_anchor": list(anchor), "face_detection": "not_performed",
        "coverage_scope": "image transform before opaque text panels, header and footer"}


def glyphs(font_path: Path, text: str) -> None:
    font = TTFont(str(font_path), fontNumber=0)
    try:
        mapping = font.getBestCmap() or {}
        missing = sorted({c for c in text if not c.isspace() and ord(c) not in mapping})
    finally:
        font.close()
    if missing:
        raise ValueError("font has no glyphs for: " + " ".join(missing))


def wrap(text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines = []
    for paragraph in text.split("\n"):
        line = ""
        for char in paragraph:
            if font.getlength(line + char) > width:
                if not line:
                    raise ValueError("textbox cannot fit one glyph")
                # Move preceding glyph with closing punctuation; never let a line start with it.
                carry = ""
                while line and (char in CLOSING or line[-1] in OPENING):
                    carry = line[-1] + carry
                    line = line[:-1]
                    if char in CLOSING:
                        break
                if not line:
                    raise ValueError("textbox too narrow for punctuation")
                lines.append(line)
                line = carry
            line += char
        lines.append(line)
    return lines


def fit(text: str, font_path: Path, width: int, height: int, minimum: int, maximum: int):
    glyphs(font_path, text)
    for size in range(maximum, minimum - 1, -1):
        font = ImageFont.truetype(str(font_path), size)
        lines = wrap(text, font, width)
        ascent, descent = font.getmetrics()
        step = math.ceil((ascent + descent) * 1.32)
        if len(lines) * step <= height and all(font.getlength(line) <= width for line in lines):
            return font, lines, step
    raise ValueError("text exceeds template capacity at minimum font size; choose another layout")


def compose(background: Path, output: Path, *, text: str, character: str, title: str,
            chapter: str, font_path: Path, style_id: str = "mist_cinema",
            width: int = 1080, height: int = 1440, draft: bool = True,
            rewrite: bool = False, overrides: dict | None = None) -> dict:
    if width < 720 or height < 960 or style_id not in STYLES:
        raise ValueError("invalid dimensions or style")
    style = {**STYLES[style_id], **(overrides or {})}
    # Long text receives more space rather than a smaller-than-contract font.
    if len(text) > 70 and not overrides:
        if style_id == "mist_cinema":
            style.update(image=(.08, .06, .92, .50), panel=(0, .50, 1, 1),
                         text=(.09, .54, .91, .86), image_fit="auto")
        elif style_id == "archive_paper":
            style["text"] = (.09, .60, .91, .86)
        elif style_id == "modern_editorial":
            style.update(image=(.08, .49, .92, .89), panel=(0, 0, 1, .49),
                         text=(.08, .16, .92, .46))
        elif style_id == "warm_memory":
            style.update(panel=(.065, .49, .935, .94), text=(.1, .55, .9, .85))
        elif style_id == "ink_whitespace":
            style.update(image=(.28, .56, .94, .87), panel=(0, 0, 1, .53),
                         text=(.09, .15, .91, .51))
        elif style_id == "dark_stage":
            style.update(image=(.24, .06, .76, .47), panel=(0, .47, 1, 1),
                         text=(.10, .50, .90, .86))
    fg, accent = style["fg"], style["accent"]
    scale = width / 1080

    def box(key):
        values = style[key]
        return tuple(round(value * (width if i % 2 == 0 else height)) for i, value in enumerate(values))

    canvas = Image.new("RGB", (width, height), style["bg"])
    with Image.open(background) as source:
        source = ImageOps.exif_transpose(source).convert("RGB")
        bounds = box("image")
        if not (0 <= bounds[0] < bounds[2] <= width and 0 <= bounds[1] < bounds[3] <= height):
            raise ValueError("image frame is outside canvas")
        fitted, position, image_fit = fit_artwork(source, bounds,
            mode=style.get("image_fit", "auto"), max_crop=style.get("max_crop", .22),
            anchor=style.get("image_anchor", (.5, .25)))
        canvas.paste(fitted, position)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle(box("panel"), fill=style["bg"])
    bounds = box("text")
    if not (0 <= bounds[0] < bounds[2] <= width and 0 <= bounds[1] < bounds[3] <= height):
        raise ValueError("text box is outside canvas")
    font, lines, step = fit(text, font_path, bounds[2]-bounds[0], bounds[3]-bounds[1],
                            max(36, round(36 * scale)), round(64 * scale))
    glyphs(font_path, character + title + chapter + "待审核用户改写人物语录")
    records = []
    for index, line in enumerate(lines):
        xy = (bounds[0], bounds[1] + index * step)
        actual = draw.textbbox(xy, line, font=font, anchor="lt")
        if actual[2] > bounds[2] or actual[3] > bounds[3]:
            raise ValueError("glyph bounds exceed text box")
        draw.text(xy, line, font=font, fill=fg, anchor="lt")
        records.append({"text": line, "xy": xy, "bbox": actual})
    small = ImageFont.truetype(str(font_path), round(27 * scale))
    label_x, label_y = round(.09 * width), round(.9 * height)
    footer = f"{character} · 《{title}》"
    if small.getlength(footer) > .82 * width:
        raise ValueError("attribution footer too long")
    draw.rectangle((0, label_y - 35*scale, width, height), fill=style["bg"])
    draw.line((label_x, label_y-28*scale, width-label_x, label_y-28*scale), fill=accent, width=2)
    draw.text((label_x, label_y), footer, font=small, fill=fg, anchor="lt")
    header = chapter[:30]
    if small.getlength(header) > .82 * width:
        raise ValueError("chapter title too long; supply a shorter display title")
    header_xy = (label_x, round(.09*height))
    header_box = draw.textbbox(header_xy, header, font=small, anchor="lt")
    draw.rectangle((header_box[0]-8, header_box[1]-8, header_box[2]+8, header_box[3]+8),
                   fill=style["bg"])
    draw.text(header_xy, header, font=small, fill=accent, anchor="lt")
    labels = []
    if draft:
        labels.append("待审核")
    if rewrite:
        labels.append("用户改写")
    if labels:
        label = " · ".join(labels)
        draw.text((label_x, round(.95*height)), label, font=small, fill=accent, anchor="lt")
    output.parent.mkdir(parents=True, exist_ok=True)
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    canvas.save(output, icc_profile=icc)
    preview = output.with_name(output.stem + "-preview.png")
    canvas.resize((360, round(360*height/width)), Image.Resampling.LANCZOS).save(preview, icc_profile=icc)
    report = {"schema_version": 1, "style_id": style_id, "width": width, "height": height,
              "font_sha256": digest_file(font_path), "background_sha256": digest_file(background),
              "output_sha256": digest_file(output), "text": text, "character": character,
              "title": title, "draft": draft, "rewrite": rewrite, "font_size": font.size,
              "text_box": bounds, "lines": records, "style": style,
              "visual_review": "pending", "preview": preview.name, "image_fit": image_fit}
    save_json(output.with_suffix(".layout.json"), report)
    return report
