from pathlib import Path

import pytest

from novel_quote_posters import bootstrap
from novel_quote_posters.storage import digest_file, load_json, save_json

SAMPLE = Path(__file__).parents[1] / "fixtures/mist_letters.txt"


def test_complete_run_resume_preserves_later_analysis_and_refuses_changed_config(tmp_path):
    run = tmp_path / "book-run"
    assert not bootstrap.initialize_run(SAMPLE, run)["reused"]
    save_json(run / "quotes.json", [{"human_edit": "must survive"}])
    assert bootstrap.initialize_run(SAMPLE, run, resume=True)["reused"]
    assert load_json(run / "quotes.json") == [{"human_edit": "must survive"}]
    with pytest.raises(ValueError, match="configuration changed"):
        bootstrap.initialize_run(SAMPLE, run, resume=True, title="different")
    with pytest.raises(ValueError, match="already exists"):
        bootstrap.initialize_run(SAMPLE, run)


def test_interrupted_ingest_reuses_completed_parse_and_never_exposes_partial_run(tmp_path, monkeypatch):
    run = tmp_path / "book-run"
    original = bootstrap.chunks

    def interrupted(*args):
        raise OSError("injected interruption after book checkpoint")

    monkeypatch.setattr(bootstrap, "chunks", interrupted)
    with pytest.raises(OSError, match="injected"):
        bootstrap.initialize_run(SAMPLE, run)
    assert not run.exists()
    stages = [p for p in (tmp_path / ".ingest-staging").iterdir() if p.is_dir()]
    book_hash = digest_file(stages[0] / "book.json")
    assert load_json(stages[0] / "checkpoint.json")["outputs"]["book.json"] == book_hash
    monkeypatch.setattr(bootstrap, "chunks", original)
    result = bootstrap.initialize_run(SAMPLE, run, resume=True)
    assert "book.json" in result["reused_phases"]
    assert digest_file(run / "book.json") == book_hash
    assert not stages[0].exists()


def test_corrupted_checkpoint_is_quarantined_and_recomputed(tmp_path, monkeypatch):
    run = tmp_path / "book-run"
    actual = bootstrap.chunks
    monkeypatch.setattr(bootstrap, "chunks", lambda *args: (_ for _ in ()).throw(OSError("stop")))
    with pytest.raises(OSError):
        bootstrap.initialize_run(SAMPLE, run)
    stage = next(p for p in (tmp_path / ".ingest-staging").iterdir() if p.is_dir())
    (stage / "book.json").write_bytes(b"damaged checkpoint")
    monkeypatch.setattr(bootstrap, "chunks", actual)
    result = bootstrap.initialize_run(SAMPLE, run, resume=True)
    assert "book.json" not in result["reused_phases"]
    assert any(p.read_bytes() == b"damaged checkpoint" for p in run.glob("damaged-*-book.json"))


def test_source_change_does_not_resume_a_different_input(tmp_path):
    source = tmp_path / "source.txt"
    source.write_bytes(SAMPLE.read_bytes())
    run = tmp_path / "run"
    bootstrap.initialize_run(source, run)
    source.write_text("第一章 不同\n另一本书。", encoding="utf-8")
    with pytest.raises(ValueError, match="configuration changed"):
        bootstrap.initialize_run(source, run, resume=True)
