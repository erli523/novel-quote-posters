import os
from pathlib import Path

import pytest
from PIL import Image

from novel_quote_posters.contracts import default_definition
from novel_quote_posters.ingest import chunks, ingest
from novel_quote_posters.layout import STYLES
from novel_quote_posters.quotes import candidates
from novel_quote_posters.storage import fingerprint, save_json


@pytest.fixture(scope="module")
def chinese_font():
    for name in [os.environ.get("POSTER_TEST_FONT", ""), "C:/Windows/Fonts/msyh.ttc",
                 "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"]:
        if name and Path(name).is_file():
            return Path(name)
    pytest.fail("Chinese test font missing; install fonts-noto-cjk or set POSTER_TEST_FONT")


@pytest.fixture
def poster_run(tmp_path):
    """Synthetic assets and fictional evidence only; never counts as real generated art."""
    run = tmp_path / "run"
    run.mkdir()
    source = run / "source.txt"
    source.write_text("第一章 山路\n林澈说：“路还在。”\n林澈说：“等天亮，再出发。”", encoding="utf-8")
    book = ingest(source, title="雾山来信")
    quotes, _ = candidates(book)
    for quote in quotes:
        quote["speaker"].update(status="explicit", character_id="lin_che", evidence=[
            {"start": book["text"].rfind("林澈说：", 0, quote["source"]["start"]),
             "end": book["text"].rfind("林澈说：", 0, quote["source"]["start"])+4,
             "text": "林澈说："}])
        quote["quote_type"] = "dialogue"
    save_json(run / "book.json", book)
    save_json(run / "chunks.json", chunks(book, 6000, 300))
    save_json(run / "quotes.json", quotes)
    save_json(run / "characters.json", {"lin_che": {"character_id": "lin_che", "name": "林澈",
                                                   "traits": [], "reference_image": None}})
    cards = []
    for index, quote in enumerate(quotes, 1):
        image = run / f"test-only-{index}.png"
        Image.new("RGB", (600, 900), "gray").save(image)
        cards.append({"card_id": f"card{index:03d}", "quote_id": quote["quote_id"],
                      "character_id": "lin_che", "style_id": "archive_paper",
                      "background": image.name, "poster": f"posters/initial-{index}.png",
                      "image_kind": "test_fixture"})
    save_json(run / "cards.json", cards)
    definition = default_definition(book, "synthetic-test", count=2, characters=["lin_che"], styles=list(STYLES))
    definition.update(allow_rewrite=True, min_quote_chars=1, input_rights_note="Self-authored fictional automated test; no actual approval")
    save_json(run / "task-contract.json", {"schema_version": 1, "version": 1, "definition": definition,
        "fingerprint": fingerprint({"version": 1, "definition": definition}), "editor": "SIMULATED TEST SETUP",
        "reason": "Synthetic fixture contract", "recorded_at": "2026-11-23T00:00:00Z"})
    return run
