import copy
import os
import subprocess
import sys
from pathlib import Path

from novel_quote_posters.analysis import apply_analysis
from novel_quote_posters.ingest import chunks, ingest
from novel_quote_posters.quotes import verify
from novel_quote_posters.storage import load_json, save_json

ROOT = Path(__file__).parents[1]


def test_application_proposals_match_authored_edge_cases_and_actual_spans():
    book = ingest(ROOT / "fixtures/dialogue_cases.txt")
    windows = chunks(book, 120, 40)
    proposal = load_json(ROOT / "fixtures/dialogue_cases.application_analysis.json")
    expected = load_json(ROOT / "fixtures/dialogue_cases.expected.json")
    quotes, characters, updated = apply_analysis(book, windows, proposal)
    assert len(quotes) == len(expected["cases"]) == 14
    assert all(w["semantic_status"] == "application_processed" for w in updated)
    for case in expected["cases"]:
        quote = next(q for q in quotes if q["source"]["start"] == case["start"])
        assert verify(book, quote)
        assert (quote["quote_exact"], quote["speaker"]["character_id"], quote["speaker"]["status"], quote["quote_type"]) == (
            case["quote_exact"], case["character_id"], case["speaker_status"], case["quote_type"])
        assert quote["review"]["status"] == "pending"
        if case["in_quotation_marks"]:
            assert book["text"][quote["source"]["start"]-1] == "“"
            assert book["text"][quote["source"]["end"]] == "”"
    assert characters["lin_che"]["name"] == characters["lin_elder"]["name"]
    assert characters["lin_che"]["character_id"] != characters["lin_elder"]["character_id"]


def test_reanalysis_withdraws_incorrect_alias_merge_without_granting_approval(tmp_path):
    run = tmp_path / "run"
    proposal = load_json(ROOT / "fixtures/dialogue_cases.application_analysis.json")
    wrong = copy.deepcopy(proposal)
    wrong["characters"]["lin_che"]["aliases"].append("周澈")
    row = next(q for q in wrong["quotes"] if q["quote_exact"] == "旧桥那边才是归路。")
    row["speaker"]["character_id"] = "lin_che"  # Deliberate semantic error, must remain pending.
    wrong_file = tmp_path / "incorrect-proposal.json"
    save_json(wrong_file, wrong)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    commands = [
        ["ingest", str(ROOT / "fixtures/dialogue_cases.txt"), "--run", str(run), "--chunk-size", "120", "--overlap", "40"],
        ["apply-analysis", str(wrong_file), "--run", str(run)],
        ["apply-analysis", str(ROOT / "fixtures/dialogue_cases.application_analysis.json"), "--run", str(run)]]
    for args in commands:
        result = subprocess.run([sys.executable, "-m", "novel_quote_posters.cli", *args], env=env,
                                capture_output=True, encoding="utf-8", check=False)
        assert result.returncode == 0, result.stdout + result.stderr
    characters = load_json(run / "characters.json")
    assert "周澈" not in characters["lin_che"]["aliases"]
    quote = next(q for q in load_json(run / "quotes.json") if q["quote_exact"] == "旧桥那边才是归路。")
    assert quote["speaker"]["character_id"] == "zhou_che"
    assert quote["review"]["status"] == "pending"
    assert len(list((run / "analysis").glob("*.json"))) == 2
    assert len(list((run / ".state/transactions").glob("*.json"))) == 2
