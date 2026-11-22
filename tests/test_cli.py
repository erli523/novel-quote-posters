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


def test_cli_image_budget_provenance_and_pending_card_progress(tmp_path):
    import json

    run = tmp_path / "run"
    cli("ingest", ROOT / "fixtures/mist_letters.txt", "--run", run, "--title", "雾山来信",
        "--chunk-size", "300", "--overlap", "80")
    assert "reused=True" in cli("ingest", ROOT / "fixtures/mist_letters.txt", "--run", run,
                                 "--title", "雾山来信", "--chunk-size", "300", "--overlap", "80", "--resume")
    cli("apply-analysis", ROOT / "fixtures/mist_analysis.json", "--run", run)
    result = json.loads(cli("prepare-cards", "--run", run, "--count", "10"))
    assert result["prepared"] == 3 and result["shortfall"] == 7
    assert json.loads(cli("status", "--run", run))["card_counts"] == {"awaiting_image": 3}
    request = run / "request.json"
    save_json(request, {"call_id": "SIMULATED", "asset_id": "TEST_REF", "kind": "reference",
                        "character_id": "lin_che", "tool": "image_gen.imagegen", "references": [],
                        "prompt": "Automated test request; no real tool call occurs."})
    assert json.loads(cli("image-plan", request, "--run", run))["action"] == "invoke"
    cli("image-fail", "--run", run, "--call", "SIMULATED", "--status", "tool_unavailable",
        "--failure-kind", "unavailable", "--note", "SIMULATED FAILURE ONLY")
    assert json.loads(cli("budget", "--run", run))["reserved_calls"] == 1
    image = run / "test-only.png"
    Image.new("RGB", (600, 900), "gray").save(image)
    from novel_quote_posters.storage import digest_file
    receipt = run / "receipt.json"
    save_json(receipt, {"source_kind": "test_fixture", "character_id": "lin_che",
                        "source_note": "Synthetic automated test image, not generated art",
                        "image_sha256": digest_file(image)})
    asset = json.loads(cli("image-import", image, "--receipt", receipt, "--run", run))
    cli("attach-image", asset["asset_id"], "--card", "card001", "--run", run)
    cli("set-reference", asset["asset_id"], "--character", "lin_che", "--run", run)
    status = json.loads(cli("status", "--run", run))
    assert status["card_counts"] == {"awaiting_image": 2, "awaiting_render": 1}
