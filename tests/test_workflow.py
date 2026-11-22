import pytest

from novel_quote_posters.workflow import Ledger


def test_dependency_changes_invalidate_only_affected_assets_and_persist(tmp_path):
    source, image, poster = [tmp_path / s for s in ["source", "image", "poster"]]
    for path in [source, image, poster]:
        path.write_bytes(path.name.encode())
    db = tmp_path / "ledger.sqlite"
    with Ledger(db) as ledger:
        ledger.put("source", source)
        ledger.put("image", image)
        ledger.put("poster", poster, ["source", "image"])
        assert ledger.valid("poster")
        source.write_bytes(b"new text")
        assert not ledger.valid("poster")  # disk changes are detected before explicit update
        ledger.put("source", source)
        assert not ledger.valid("poster")
        assert ledger.valid("image")  # typography changes do not regenerate imagery
        ledger.put("poster", poster, ["source", "image"])
    with Ledger(db) as ledger:
        assert ledger.valid("poster")
        assert any(e["type"] == "invalidated" for e in ledger.events())
        image.unlink()
        assert not ledger.valid("poster")


def test_budget_resume_and_unknown_outcomes(tmp_path):
    with Ledger(tmp_path / "ledger.sqlite", limit=2) as ledger:
        assert ledger.reserve("one", "hash")["action"] == "invoke"
        assert ledger.reserve("one", "hash")["action"] == "reconcile"
        ledger.resolve_call("one", "unknown_outcome")
        assert ledger.reserve("one", "hash")["action"] == "reconcile"
        with pytest.raises(ValueError, match="changed"):
            ledger.reserve("one", "different")
        assert ledger.reserve("two", "hash2")["action"] == "invoke"
        with pytest.raises(ValueError, match="exhausted"):
            ledger.reserve("three", "hash3")
        with pytest.raises(ValueError, match="committed"):
            ledger.resolve_call("two", "succeeded", "missing-asset")
        image = tmp_path / "actual-image"
        image.write_bytes(b"asset")
        ledger.put("image", image)
        ledger.resolve_call("two", "succeeded", "image")
        assert ledger.reserve("two", "hash2")["action"] == "reuse"


def test_invalid_parent_rolls_back_asset_and_events(tmp_path):
    path = tmp_path / "asset"
    path.write_text("v1")
    with Ledger(tmp_path / "ledger.sqlite") as ledger:
        ledger.put("a", path)
        count = len(ledger.events())
        path.write_text("v2")
        with pytest.raises(ValueError):
            ledger.put("a", path, ["missing"])
        assert len(ledger.events()) == count
        assert not ledger.valid("a")


def test_cycles_are_rejected(tmp_path):
    path = tmp_path / "asset"
    path.write_text("v1")
    with Ledger(tmp_path / "ledger.sqlite") as ledger:
        ledger.put("a", path)
        ledger.put("b", path, ["a"])
        with pytest.raises(ValueError, match="cycle"):
            ledger.put("a", path, ["b"])
