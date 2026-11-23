import pytest
from PIL import Image

from novel_quote_posters.layout import STYLES, compose, fit, fit_artwork, glyphs


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
