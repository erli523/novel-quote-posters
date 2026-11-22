import os
import subprocess
import sys
import zipfile
from pathlib import Path

from PIL import Image

from novel_quote_posters.storage import load_json, save_json

ROOT = Path(__file__).parents[1]


def cli(*args, expected=0):
    result = subprocess.run([sys.executable, "-m", "novel_quote_posters.cli", *map(str, args)],
                            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                            capture_output=True, encoding="utf-8", check=False)
    assert result.returncode == expected, result.stdout + result.stderr
    return result.stdout + result.stderr


def test_cli_source_analysis_render_reuse_review_export_recovery(tmp_path, chinese_font):
    run = tmp_path / "run"
    cli("ingest", ROOT / "fixtures/mist_letters.txt", "--run", run, "--title", "雾山来信",
        "--chunk-size", "300", "--overlap", "80")
    cli("apply-analysis", ROOT / "fixtures/mist_analysis.json", "--run", run)
    quotes = load_json(run / "quotes.json")
    quote = next(q for q in quotes if q["selection"].get("selected"))
    Image.new("RGB", (600, 900), "gray").save(run / "test-only.png")
    save_json(run / "cards.json", [{"card_id": "card001", "quote_id": quote["quote_id"],
                                   "character_id": "lin_che", "style_id": "mist_cinema",
                                   "background": "test-only.png", "image_kind": "test_fixture"}])
    assert "Rendered 1; reused 0" in cli("render", "--run", run, "--font", chinese_font)
    assert "Rendered 0; reused 1" in cli("render", "--run", run, "--font", chinese_font)
    page = run / "review.html"
    cli("review", "--run", run, "--output", page)
    assert "attestation" in cli("apply-review", page.with_suffix(".targets.json"), "--run", run, expected=2)
    output = tmp_path / "export"
    cli("export", "--run", run, "--output", output, "--draft")
    with zipfile.ZipFile(output / "draft-review.zip") as archive:
        assert archive.testzip() is None
        assert "book.json" not in archive.namelist()
    assert "No pending" in cli("recover", "--run", run)
    assert "draft flag" in cli("export", "--run", run, "--output", tmp_path / "final", expected=2)
