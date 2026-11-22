import pytest
from PIL import Image

from novel_quote_posters.layout import STYLES, compose, fit, glyphs


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
