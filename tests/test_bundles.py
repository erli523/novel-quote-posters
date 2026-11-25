import json
import zipfile

import pytest

from novel_quote_posters import export
from novel_quote_posters.bundles import verify_archive
from novel_quote_posters.rendering import render_cards
from novel_quote_posters.storage import digest_file, load_json, save_json


def test_archive_is_portable_deterministic_and_inventory_covers_every_payload(poster_run, chinese_font, tmp_path):
    render_cards(poster_run, chinese_font)
    first, second = tmp_path / "first", tmp_path / "second"
    result = export.export_bundle(poster_run, first, draft=True)
    export.export_bundle(poster_run, second, draft=True)
    assert digest_file(first / "draft-review.zip") == digest_file(second / "draft-review.zip")
    assert verify_archive(first / "draft-review.zip")["integrity"] == "passed"
    with zipfile.ZipFile(first / "draft-review.zip") as archive:
        cards = json.loads(archive.read("cards.json"))
        for card in cards:
            assert card["poster"] in archive.namelist()
            assert card["layout_report"] in archive.namelist()
            report = json.loads(archive.read(card["layout_report"]))
            assert "font" not in report["render_options"]
            assert report["line_break_policy"] == "punctuation-first-2"
            assert report["auxiliary_text"]["footer"]["font_size"] == 36
            assert card["background"]["available_in"] == "original_run"
        assert {row["path"] for row in result["files"]} | {"manifest.json"} == set(archive.namelist())
        assert "book.json" not in archive.namelist()
    assert export.export_bundle(poster_run, first, draft=True, resume=True)["reused"]


def test_completed_zip_recovers_after_interruption_before_report_without_rebuilding(poster_run, chinese_font, tmp_path, monkeypatch):
    render_cards(poster_run, chinese_font)
    output = tmp_path / "output"
    actual = export.write_archive

    def interrupted(*args):
        actual(*args)
        raise OSError("injected loss after complete zip before report")

    monkeypatch.setattr(export, "write_archive", interrupted)
    with pytest.raises(OSError, match="injected"):
        export.export_bundle(poster_run, output, draft=True)
    assert not output.exists()
    monkeypatch.setattr(export, "write_archive", lambda *args: pytest.fail("valid ZIP was unnecessarily rebuilt"))
    result = export.export_bundle(poster_run, output, draft=True, resume=True)
    assert result["reused"] and verify_archive(output / "draft-review.zip")["integrity"] == "passed"


def test_input_change_mid_export_never_publishes_old_order(poster_run, chinese_font, tmp_path, monkeypatch):
    render_cards(poster_run, chinese_font)
    output = tmp_path / "output"
    actual = export.write_archive

    def change_order(*args):
        actual(*args)
        save_json(poster_run / "cards.json", list(reversed(load_json(poster_run / "cards.json"))))

    monkeypatch.setattr(export, "write_archive", change_order)
    with pytest.raises(ValueError, match="inputs changed"):
        export.export_bundle(poster_run, output, draft=True)
    assert not output.exists()
    monkeypatch.setattr(export, "write_archive", actual)
    report = export.export_bundle(poster_run, output, draft=True, resume=True)
    assert report["assets"][0]["card_id"] == "card002"


def test_unrelated_or_runtime_data_is_not_exported(poster_run, chinese_font, tmp_path):
    characters = load_json(poster_run / "characters.json")
    characters["unused"] = {"name": "unused", "private": "UNRELATED_PRIVATE_MARKER"}
    characters["lin_che"]["runtime_secret"] = "RUNTIME_PRIVATE_MARKER"
    save_json(poster_run / "characters.json", characters)
    render_cards(poster_run, chinese_font)
    cards = load_json(poster_run / "cards.json")
    for card in cards:
        layout = poster_run / card["poster"]
        report = load_json(layout.with_suffix(".layout.json"))
        report["runtime_secret"] = "UNEXPECTED_LAYOUT_PRIVATE_MARKER"
        report["image_fit"]["runtime_secret"] = "NESTED_IMAGE_PRIVATE_MARKER"
        report["auxiliary_text"]["runtime_secret"] = "NESTED_LABELS_PRIVATE_MARKER"
        report["auxiliary_text"]["footer"]["font_path"] = "NESTED_FONT_PRIVATE_MARKER"
        save_json(layout.with_suffix(".layout.json"), report)
    output = tmp_path / "output"
    export.export_bundle(poster_run, output, draft=True)
    with zipfile.ZipFile(output / "draft-review.zip") as archive:
        assert set(json.loads(archive.read("characters.json"))) == {"lin_che"}
        for name in archive.namelist():
            if name.endswith(".json"):
                text = archive.read(name).decode()
                assert "PRIVATE_MARKER" not in text
                assert str(chinese_font) not in text


@pytest.mark.parametrize("name", ["../escape", "C:/escape", "..\\escape"])
def test_verifier_refuses_unsafe_members(tmp_path, name):
    path = tmp_path / "malicious.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, b"untrusted")
    with pytest.raises(ValueError, match="unsafe"):
        verify_archive(path)
    assert not (tmp_path.parent / "escape").exists()


def test_changed_archive_payload_fails_integrity(poster_run, chinese_font, tmp_path):
    render_cards(poster_run, chinese_font)
    output = tmp_path / "output"
    export.export_bundle(poster_run, output, draft=True)
    changed = tmp_path / "changed.zip"
    with zipfile.ZipFile(output / "draft-review.zip") as original, zipfile.ZipFile(changed, "w") as target:
        for name in original.namelist():
            target.writestr(name, b"altered" if name == "quotes.csv" else original.read(name))
    with pytest.raises(ValueError, match="checksum mismatch"):
        verify_archive(changed)


def test_export_history_detects_stale_inputs_and_keeps_prior_archive(poster_run, chinese_font, tmp_path):
    render_cards(poster_run, chinese_font)
    output = tmp_path / "output"
    export.export_bundle(poster_run, output, draft=True)
    previous = digest_file(output / "draft-review.zip")
    assert export.export_status(poster_run)["state"] == "current"
    save_json(poster_run / "cards.json", list(reversed(load_json(poster_run / "cards.json"))))
    assert export.export_status(poster_run)["state"] == "invalidated"
    with pytest.raises(ValueError, match="inputs changed"):
        export.export_bundle(poster_run, output, draft=True, resume=True)
    assert digest_file(output / "draft-review.zip") == previous
