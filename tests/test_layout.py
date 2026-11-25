import pytest
from PIL import Image, ImageFont

from novel_quote_posters.layout import (
    CLOSING,
    OPENING,
    STYLES,
    compose,
    fit,
    fit_artwork,
    glyphs,
    wrap,
)


@pytest.mark.parametrize("style", STYLES)
def test_styles_render_chinese_with_safe_text_boxes(tmp_path, chinese_font, style):
    source = tmp_path / "synthetic-only.png"
    # This is a flat test fixture, explicitly not a generated character illustration.
    Image.new("RGB", (600, 900), "#607080").save(source)
    output = tmp_path / f"{style}.png"
    report = compose(source, output, text="山不会替你走路，但它会告诉你，路还在。",
                     character="林澈", title="雾山来信", chapter="第一章 山路",
                     font_path=chinese_font, style_id=style)
    assert report["font_size"] >= 36
    assert "".join(line["text"] for line in report["lines"]) == report["text"]
    assert report["draft"] and report["visual_review"] == "pending"
    for line in report["lines"]:
        assert line["text"][0] not in "，。！？；：、）"
    with Image.open(output) as image:
        assert image.size == (1080, 1440)
        assert image.info["icc_profile"]


def test_oversized_text_rejects_instead_of_shrinking_forever(chinese_font):
    with pytest.raises(ValueError, match="capacity"):
        fit("很长的句子" * 100, chinese_font, 300, 300, 36, 60)
    with pytest.raises(ValueError, match="no glyphs"):
        glyphs(chinese_font, chr(0x10FFFF))


def test_empty_chapter_leaves_artwork_uncovered(tmp_path, chinese_font):
    source = tmp_path / "synthetic.png"
    color = (96, 112, 128)
    Image.new("RGB", (1200, 900), color).save(source)
    output = tmp_path / "without-chapter.png"
    report = compose(source, output, text="山不会替你走路，但它会告诉你，路还在。",
                     character="林澈", title="雾山来信", chapter="",
                     font_path=chinese_font, style_id="warm_memory")
    assert "header" not in report["auxiliary_text"]
    assert report["auxiliary_text"]["footer"]["text"] == "林澈 · 《雾山来信》"
    with Image.open(output) as image:
        # An empty chapter formerly left a small opaque rectangle here.
        assert image.crop((89, 122, 106, 139)).getextrema() == tuple((v, v) for v in color)


@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("length", [70, 100])
def test_long_sentence_capacity(tmp_path, chinese_font, style, length):
    source = tmp_path / "synthetic.png"
    Image.new("RGB", (600, 900), "gray").save(source)
    report = compose(source, tmp_path / "poster.png", text="路" * length,
                     character="林澈", title="雾山来信", chapter="第一章 山路",
                     font_path=chinese_font, style_id=style)
    assert report["font_size"] >= 36


def test_portrait_in_wide_frame_preserves_whole_source_in_auto_mode():
    source = Image.new("RGB", (120, 240), "navy")
    source.putpixel((0, 0), (255, 0, 0))
    source.putpixel((119, 239), (0, 255, 0))
    picture, position, report = fit_artwork(source, (10, 20, 250, 140))
    assert report["applied"] == "contain"
    assert picture.size == (60, 120) and position == (100, 20)
    assert report["source_crop_box"] == [0, 0, 120, 240]
    assert report["retained_source_area"] == 1
    assert picture.getpixel((0, 0))[0] > 0
    assert picture.getpixel((59, 119))[1] > 0


def test_small_cover_crop_records_exact_source_region_and_preserves_aspect():
    picture, position, report = fit_artwork(Image.new("RGB", (100, 100)), (0, 0, 100, 80))
    assert report["applied"] == "cover" and picture.size == (100, 80)
    assert report["source_crop_box"] == [0, 5, 100, 85]
    assert report["retained_source_area"] == pytest.approx(.8)
    assert position == (0, 0)


def test_explicit_cover_and_anchor_remain_available_without_hiding_crop():
    source = Image.new("RGB", (100, 200), "blue")
    source.paste("red", (0, 0, 100, 100))
    top, _, report = fit_artwork(source, (0, 0, 100, 100), mode="cover", anchor=(.5, 0))
    bottom, _, other = fit_artwork(source, (0, 0, 100, 100), mode="cover", anchor=(.5, 1))
    assert top.getpixel((50, 50)) == (255, 0, 0)
    assert bottom.getpixel((50, 50)) == (0, 0, 255)
    assert report["retained_source_area"] == .5
    assert other["source_crop_box"] == [0, 100, 100, 200]
    assert report["face_detection"] == "not_performed"


@pytest.mark.parametrize("options", [{"mode": "stretch"}, {"mode": []}, {"max_crop": -1},
                                    {"max_crop": float("nan")}, {"anchor": [0, 2]},
                                    {"anchor": [True, .5]}])
def test_invalid_image_fit_parameters_are_rejected(options):
    with pytest.raises(ValueError):
        fit_artwork(Image.new("RGB", (100, 200)), (0, 0, 100, 100), **options)


def test_quote_uses_clause_boundary_without_changing_a_character(chinese_font):
    font = ImageFont.truetype(str(chinese_font), 64)
    text = "山不会替你走路，但它会告诉你，路还在。"
    lines = wrap(text, font, int(font.getlength("路"*13))+1)
    assert lines == ["山不会替你走路，", "但它会告诉你，路还在。"]
    assert "".join(lines) == text


def test_fit_can_reduce_size_to_keep_two_whole_clauses(chinese_font):
    font = ImageFont.truetype(str(chinese_font), 64)
    text = "有些告别没有声音，却会在往后的每一场雨里响起。"
    fitted, lines, _ = fit(text, chinese_font, int(font.getlength("路"*13))+1, 302, 36, 64)
    assert fitted.size < 64
    assert lines == ["有些告别没有声音，", "却会在往后的每一场雨里响起。"]


def test_quote_and_bracket_clusters_never_start_generated_lines(chinese_font):
    font = ImageFont.truetype(str(chinese_font), 48)
    text = "“他说（路还在！）”甲说：“好。”"
    lines = wrap(text, font, int(font.getlength("路"*6))+1)
    assert "".join(lines) == text
    assert all(line[0] not in CLOSING and line[-1] not in OPENING for line in lines)


def test_punctuation_cluster_too_wide_at_large_size_can_fit_at_permitted_smaller_size(chinese_font):
    font = ImageFont.truetype(str(chinese_font), 64)
    width = int(font.getlength("路！"))
    with pytest.raises(ValueError, match="punctuation"):
        wrap("路！”", font, width)
    fitted, lines, _ = fit("路！”", chinese_font, width, 160, 36, 64)
    assert 36 <= fitted.size < 64 and lines == ["路！”"]


def test_ascii_word_that_fits_is_kept_whole_with_spaces_preserved(chinese_font):
    font = ImageFont.truetype(str(chinese_font), 48)
    text = "这是 OpenAI 的资料"
    width = int(font.getlength("这是 Open"))
    lines = wrap(text, font, width)
    assert "".join(lines) == text
    assert any("OpenAI" in line for line in lines)
    assert all(font.getlength(line) <= width for line in lines)


def test_long_ascii_word_falls_back_to_lossless_character_wrap(chinese_font):
    font = ImageFont.truetype(str(chinese_font), 48)
    text = "VeryLongUnbrokenWord"
    width = int(font.getlength("MMMM"))
    lines = wrap(text, font, width)
    assert len(lines) > 1 and "".join(lines) == text
    assert all(font.getlength(line) <= width for line in lines)


def test_explicit_paragraph_and_blank_line_are_preserved(chinese_font):
    font = ImageFont.truetype(str(chinese_font), 48)
    assert wrap("第一段\n\n第二段", font, 900) == ["第一段", "", "第二段"]


def test_auxiliary_text_is_recorded_at_readable_size_and_header_is_not_truncated(tmp_path, chinese_font):
    source = tmp_path / "synthetic.png"
    Image.new("RGB", (600, 900), "gray").save(source)
    chapter = "Chapter " + "A"*32
    report = compose(source, tmp_path / "poster.png", text="路还在。", character="林澈",
                     title="雾山来信", chapter=chapter, font_path=chinese_font)
    assert report["auxiliary_text"]["header"]["text"] == chapter
    assert report["auxiliary_text"]["footer"]["font_size"] == 36
    assert report["line_break_policy"] == "punctuation-first-1"
    for label in report["auxiliary_text"].values():
        left, top, right, bottom = label["bbox"]
        assert 0 <= left <= right <= 1080 and 0 <= top <= bottom <= 1440


def test_overlong_header_is_rejected_instead_of_silently_truncated(tmp_path, chinese_font):
    source = tmp_path / "synthetic.png"
    Image.new("RGB", (600, 900), "gray").save(source)
    with pytest.raises(ValueError, match="chapter title too long"):
        compose(source, tmp_path / "poster.png", text="路还在。", character="林澈",
                title="雾山来信", chapter="超长"*40, font_path=chinese_font)


@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("length", [70, 100])
def test_punctuated_boundary_lengths_remain_lossless_and_within_capacity(tmp_path, chinese_font, style, length):
    source = tmp_path / "synthetic.png"
    Image.new("RGB", (600, 900), "gray").save(source)
    text = ("路还在，灯也还在。"*12)[:length]
    report = compose(source, tmp_path / "poster.png", text=text, character="林澈", title="雾山来信",
                     chapter="第一章 山路", font_path=chinese_font, style_id=style)
    assert "".join(line["text"] for line in report["lines"]) == text
    assert report["font_size"] >= 36


@pytest.mark.parametrize("style", STYLES)
def test_overcapacity_punctuated_text_is_explicitly_rejected(tmp_path, chinese_font, style):
    source = tmp_path / "synthetic.png"
    Image.new("RGB", (600, 900), "gray").save(source)
    with pytest.raises(ValueError, match="capacity"):
        compose(source, tmp_path / "poster.png", text="路还在，灯也还在。"*34, character="林澈",
                title="雾山来信", chapter="第一章 山路", font_path=chinese_font, style_id=style)
