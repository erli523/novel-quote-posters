import zipfile

import pytest
from PIL import Image

from novel_quote_posters.export import export_bundle
from novel_quote_posters.ingest import index_text
from novel_quote_posters.layout import compose
from novel_quote_posters.quotes import make_quote
from novel_quote_posters.storage import save_json, sha256


def setup_run(root, font):
    root.mkdir()
    text = "第一章 山路\n林澈说：“路还在。”"
    (root / "source.txt").write_bytes(text.encode())
    source = index_text(text, sha256(text.encode()), "雾山来信", format="txt")
    quote = make_quote(source, "路还在。")
    save_json(root / "book.json", source)
    save_json(root / "quotes.json", [quote])
    save_json(root / "characters.json", {"lin_che": {"name": "林澈"}})
    card = {"card_id": "card001", "character_id": "lin_che", "quote_id": quote["quote_id"],
            "poster": "posters/card001.png", "background": "synthetic.png",
            "style_id": "archive_paper", "image_kind": "test_fixture"}
    save_json(root / "cards.json", [card])
    image = root / "synthetic.png"
    Image.new("RGB", (600, 900), "gray").save(image)
    compose(image, root / card["poster"], text=quote["display_text"], character="林澈",
            title="雾山来信", chapter="第一章 山路", font_path=font,
            style_id="archive_paper")
    return source, quote, card


def test_draft_export_omits_full_source_and_validates_zip(tmp_path, chinese_font):
    run = tmp_path / "run"
    setup_run(run, chinese_font)
    output = tmp_path / "output"
    report = export_bundle(run, output, draft=True)
    assert report["draft"] and report["review_status"] == "pending"
    with zipfile.ZipFile(output / "draft-review.zip") as archive:
        assert archive.testzip() is None
        assert "book.json" not in archive.namelist()
        assert "source.txt" not in archive.namelist()
        assert "quotes.csv" in archive.namelist()
        assert "待审核" in archive.read("发布说明.txt").decode()


def test_final_export_refuses_draft_and_unreviewed_cards(tmp_path, chinese_font):
    run = tmp_path / "run"
    setup_run(run, chinese_font)
    with pytest.raises(ValueError, match="draft flag"):
        export_bundle(run, tmp_path / "final", draft=False)


def test_modified_quote_cannot_reuse_old_poster(tmp_path, chinese_font):
    run = tmp_path / "run"
    _, quote, _ = setup_run(run, chinese_font)
    quote["display_text"] = "山路还在。"
    quote["text_mode"] = "rewrite"
    save_json(run / "quotes.json", [quote])
    with pytest.raises(ValueError, match="stale"):
        export_bundle(run, tmp_path / "output", draft=True, allow_rewrite=True)


def test_outside_run_paths_are_rejected(tmp_path, chinese_font):
    run = tmp_path / "run"
    _, _, card = setup_run(run, chinese_font)
    card["poster"] = "../outside.png"
    save_json(run / "cards.json", [card])
    with pytest.raises(ValueError, match="escapes"):
        export_bundle(run, tmp_path / "output", draft=True)


def test_tampered_source_and_background_are_detected(tmp_path, chinese_font):
    run = tmp_path / "run"
    setup_run(run, chinese_font)
    original = (run / "source.txt").read_bytes()
    (run / "source.txt").write_bytes(b"tampered source")
    with pytest.raises(ValueError, match="source file hash"):
        export_bundle(run, tmp_path / "output", draft=True)
    (run / "source.txt").write_bytes(original)
    Image.new("RGB", (600, 900), "red").save(run / "synthetic.png")
    with pytest.raises(ValueError, match="background changed"):
        export_bundle(run, tmp_path / "output", draft=True)
