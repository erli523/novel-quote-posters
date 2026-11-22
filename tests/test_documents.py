import pytest

from novel_quote_posters import documents
from novel_quote_posters.storage import load_json, save_json
from novel_quote_posters.workflow import Ledger


def test_interrupted_multi_file_write_recovers_before_next_read(tmp_path, monkeypatch):
    run = tmp_path / "run"
    run.mkdir()
    save_json(run / "quotes.json", ["old quotes"])
    save_json(run / "cards.json", ["old cards"])
    actual = documents.save_json

    def interrupted(path, value):
        if path == run / "cards.json":
            raise OSError("injected power loss before second materialization")
        actual(path, value)

    monkeypatch.setattr(documents, "save_json", interrupted)
    with pytest.raises(OSError, match="injected"):
        documents.commit_documents(run, {"quotes.json": ["new quotes"], "cards.json": ["new cards"]},
                                   reason="fault_injection")
    assert (run / ".state/pending-documents.json").exists()
    assert load_json(run / "quotes.json") == ["new quotes"]
    assert load_json(run / "cards.json") == ["old cards"]
    monkeypatch.setattr(documents, "save_json", actual)
    assert documents.recover_documents(run)
    assert not documents.recover_documents(run)
    assert load_json(run / "cards.json") == ["new cards"]
    history = list((run / ".state/transactions").glob("*.json"))
    assert load_json(history[0])["before"] == {"quotes.json": ["old quotes"], "cards.json": ["old cards"]}


def test_transaction_cannot_escape_run(tmp_path):
    with pytest.raises(ValueError, match="unsupported"):
        documents.commit_documents(tmp_path, {"../escape.json": {}}, reason="bad")
    assert not (tmp_path.parent / "escape.json").exists()


def test_crash_after_files_commit_reconciles_event_without_overwriting_manual_edits(tmp_path, monkeypatch):
    run = tmp_path / "run"
    run.mkdir()
    actual = documents._recover

    def crash_after_pointer_delete(directory, ledger):
        result = actual(directory, ledger)
        if result:
            raise OSError("injected SQLite commit loss")
        return result

    monkeypatch.setattr(documents, "_recover", crash_after_pointer_delete)
    with pytest.raises(OSError, match="commit loss"):
        documents.commit_documents(run, {"quotes.json": ["written"]}, reason="test")
    assert not (run / ".state/pending-documents.json").exists()
    with Ledger(run / ".state/ledger.sqlite") as ledger:
        assert not ledger.events()
    save_json(run / "quotes.json", ["later manual edit"])
    monkeypatch.setattr(documents, "_recover", actual)
    assert documents.recover_documents(run)
    assert load_json(run / "quotes.json") == ["later manual edit"]
    with Ledger(run / ".state/ledger.sqlite") as ledger:
        assert ledger.events()[0]["payload"]["reason"] == "reconciled_durable_head"
