"""Synthetic software tests; sample pictures and simulated signatures are not real approvals."""

import copy
import json
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from novel_quote_posters import samples
from novel_quote_posters.images import budget_summary, register_image, set_reference
from novel_quote_posters.storage import digest_file, load_json, save_json
from novel_quote_posters.style_review import style_review_status, write_style_review_page


@pytest.fixture
def sample_plan(poster_run, chinese_font):
    asset = register_image(
        poster_run,
        poster_run / "test-only-1.png",
        {
            "source_kind": "user_provided",
            "character_id": "lin_che",
            "image_sha256": digest_file(poster_run / "test-only-1.png"),
            "source_note": "SIMULATED SOFTWARE TEST REPLACEMENT; not real generated art",
        },
    )
    set_reference(poster_run, asset["asset_id"], "lin_che")
    quote = load_json(poster_run / "quotes.json")[0]
    plan = samples.sample_template(poster_run, quote["quote_id"], chinese_font)
    plan.update(
        editor="SIMULATED APPLICATION TEST",
        reason="Explicit synthetic six-style plan; never actual approval",
    )
    for row in plan["styles"]:
        row.update(
            asset_id=asset["asset_id"],
            font_license_note="Installed software-test font; font bytes not distributed",
            reuse_reason="SIMULATED single registered test picture reused only to verify deterministic composition",
        )
    return poster_run, plan


def test_template_requires_explicit_mapping_and_binds_selected_quote(sample_plan, chinese_font):
    run, plan = sample_plan
    blank = samples.sample_template(run, plan["quote_id"], chinese_font)
    assert all(row["asset_id"] == "" and row["reuse_reason"] == "" for row in blank["styles"])
    assert blank["draft"] and blank["editor"] == ""
    with pytest.raises(ValueError, match="named editor"):
        samples.render_samples(run, blank, run / "samples")
    assert not (run / ".state/sample-renders").exists()


def test_complete_render_resumes_reuses_other_version_and_enters_supplementary_review(
    sample_plan, tmp_path, monkeypatch, capsys
):
    from novel_quote_posters.cli import main

    run, plan = sample_plan
    before = {
        name: digest_file(run / name) for name in ["cards.json", "quotes.json", "characters.json"]
    }
    result = samples.render_samples(run, plan, run / "samples-v1")
    assert result["rendered"] == 6 and result["reused"] == 0 and result["image_tool_calls"] == 0
    assert samples.validate_managed_samples(run, run / "samples-v1")["styles"] == 6
    manifest = load_json(run / "samples-v1/manifest.json")
    assert len(manifest["styles"]) == 6 and manifest["human_review"] == "pending"
    assert not any(
        "font" in row and row["font"].startswith(("C:", "/")) for row in manifest["styles"]
    )
    assert before == {name: digest_file(run / name) for name in before}
    original = {row["style_id"]: row["poster_sha256"] for row in manifest["styles"]}
    with pytest.raises(ValueError, match="already exists"):
        samples.render_samples(run, plan, run / "samples-v1")
    assert samples.render_samples(run, plan, run / "samples-v1", resume=True)["published_reused"]
    second = samples.render_samples(run, plan, run / "samples-v2")
    assert second["rendered"] == 0 and second["reused"] == 6
    assert original == {
        row["style_id"]: row["poster_sha256"]
        for row in load_json(run / "samples-v2/manifest.json")["styles"]
    }
    write_style_review_page(run, "samples-v2", tmp_path / "six.html")
    assert style_review_status(run)["state"] == "pending"
    assert not (run / "six-style-review.json").exists()
    assert budget_summary(run)["reserved_calls"] == 0
    plan_file = tmp_path / "plan.json"
    save_json(plan_file, plan)
    monkeypatch.setattr(
        "sys.argv",
        [
            "novel-posters",
            "render-samples",
            str(plan_file),
            "--run",
            str(run),
            "--output",
            str(run / "samples-v2"),
            "--resume",
        ],
    )
    main()
    assert json.loads(capsys.readouterr().out)["reused"] == 6


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.update(schema_version=True),
        lambda p: p.update(editor=""),
        lambda p: p["styles"].pop(),
        lambda p: p["styles"].__setitem__(0, p["styles"][1]),
        lambda p: p["styles"][-1].update(asset_id="unknown"),
        lambda p: p["styles"][-1].update(font="missing-font.ttf"),
        lambda p: p["styles"][-1].update(reuse_reason=""),
        lambda p: p["styles"][-1].update(layout_overrides={"max_crop": float("nan")}),
        lambda p: p["styles"][-1].update(layout_overrides={"font_path": "unapproved"}),
        lambda p: p["styles"][-1].update(layout_overrides={"text": [0, 0.2, 0.8, 0.3]}),
        lambda p: p.update(expected_content_fingerprint="0" * 64),
    ],
)
def test_invalid_last_row_or_stale_plan_publishes_nothing_and_renders_nothing(
    sample_plan, mutation
):
    run, plan = sample_plan
    mutation(plan)
    with pytest.raises(ValueError):
        samples.render_samples(run, plan, run / "samples")
    assert not (run / "samples").exists() and not (run / ".state/sample-renders").exists()


def test_single_framing_change_only_recomposes_one_sample_and_invalidates_review(
    sample_plan, tmp_path
):
    run, plan = sample_plan
    samples.render_samples(run, plan, run / "samples-v1")
    write_style_review_page(run, "samples-v1", tmp_path / "review.html")
    changed = copy.deepcopy(plan)
    changed["styles"][3]["layout_overrides"] = {
        "image": [0.18, 0.39, 0.94, 0.87],
        "image_fit": "cover",
        "image_anchor": [0.5, 0.05],
    }
    with pytest.raises(ValueError, match="plan changed"):
        samples.render_samples(run, changed, run / "samples-v1", resume=True)
    result = samples.render_samples(run, changed, run / "samples-v2")
    assert result["rendered"] == 1 and result["reused"] == 5
    old = load_json(run / "samples-v1/manifest.json")
    new = load_json(run / "samples-v2/manifest.json")
    for a, b in zip(old["styles"], new["styles"]):
        assert (a["poster_sha256"] == b["poster_sha256"]) == (a["style_id"] != "ink_whitespace")
        with (
            Image.open(run / "samples-v1" / a["path"]) as old_image,
            Image.open(run / "samples-v2" / b["path"]) as new_image,
        ):
            same_pixels = (
                ImageChops.difference(old_image.convert("RGB"), new_image.convert("RGB")).getbbox()
                is None
            )
        assert same_pixels == (a["style_id"] != "ink_whitespace")
    write_style_review_page(run, "samples-v2", tmp_path / "review-new.html")
    assert style_review_status(run)["state"] == "pending"


def test_incomplete_cache_and_interrupted_release_recover_without_recomposing_completed_samples(
    sample_plan, monkeypatch
):
    run, plan = sample_plan
    actual = samples._render_one
    calls = 0

    def interrupted(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("SIMULATED interruption before third sample")
        return actual(*args, **kwargs)

    monkeypatch.setattr(samples, "_render_one", interrupted)
    with pytest.raises(OSError, match="interruption"):
        samples.render_samples(run, plan, run / "samples")
    assert not (run / "samples").exists()
    with pytest.raises(ValueError, match="unfinished"):
        samples.render_samples(run, plan, run / "samples")
    monkeypatch.setattr(samples, "_render_one", actual)
    resumed = samples.render_samples(run, plan, run / "samples", resume=True)
    assert resumed["reused"] == 2 and resumed["rendered"] == 4
    assert list((run / ".sample-staging").rglob("failure-*.json"))


def test_pixel_cache_corruption_is_retained_and_only_one_repaired_for_new_version(sample_plan):
    run, plan = sample_plan
    samples.render_samples(run, plan, run / "samples-v1")
    cached = next(
        p
        for p in (run / ".state/sample-renders").glob("mist_cinema-*.png")
        if not p.stem.endswith("-preview")
    )
    cached.write_bytes(b"SIMULATED damaged cached PNG")
    result = samples.render_samples(run, plan, run / "samples-v2")
    assert result["rendered"] == 1 and result["reused"] == 5
    assert cached.read_bytes() == b"SIMULATED damaged cached PNG"
    assert list((run / ".state/sample-renders").glob("mist_cinema-*-attempt2.png"))


def test_damaged_published_directory_is_not_overwritten(sample_plan):
    run, plan = sample_plan
    samples.render_samples(run, plan, run / "samples")
    path = run / "samples/ink_whitespace.png"
    path.write_bytes(b"SIMULATED damaged published file")
    with pytest.raises(ValueError, match="damaged"):
        samples.render_samples(run, plan, run / "samples", resume=True)
    assert path.read_bytes() == b"SIMULATED damaged published file"


def test_changed_inputs_before_publication_leave_only_checkpoint(sample_plan, monkeypatch):
    run, plan = sample_plan
    actual = samples._check_files

    def change_after_files_checked(*args, **kwargs):
        result = actual(*args, **kwargs)
        characters = load_json(run / "characters.json")
        characters["lin_che"]["traits"].append(
            {"kind": "design_choice", "value": "SIMULATED concurrent coat change"}
        )
        save_json(run / "characters.json", characters)
        return result

    monkeypatch.setattr(samples, "_check_files", change_after_files_checked)
    with pytest.raises(ValueError, match="content changed"):
        samples.render_samples(run, plan, run / "samples")
    assert not (run / "samples").exists() and list(
        (run / ".sample-staging").rglob("build-seal.json")
    )


def test_loss_after_publication_resumes_existing_bytes(sample_plan, monkeypatch):
    run, plan = sample_plan
    actual = samples.os.rename

    def lost_response(*args):
        actual(*args)
        raise OSError("SIMULATED return lost after directory publication")

    monkeypatch.setattr(samples.os, "rename", lost_response)
    with pytest.raises(OSError, match="return lost"):
        samples.render_samples(run, plan, run / "samples")
    before = digest_file(run / "samples/manifest.json")
    monkeypatch.setattr(samples.os, "rename", actual)
    assert samples.render_samples(run, plan, run / "samples", resume=True)["published_reused"]
    assert digest_file(run / "samples/manifest.json") == before


def test_font_and_unsealed_file_changes_invalidate_managed_review(sample_plan, tmp_path):
    import shutil

    run, plan = sample_plan
    local = tmp_path / "font.ttc"
    shutil.copy2(Path(plan["styles"][0]["font"]), local)
    for row in plan["styles"]:
        row["font"] = str(local)
    samples.render_samples(run, plan, run / "samples")
    write_style_review_page(run, "samples", tmp_path / "review.html")
    assert style_review_status(run)["state"] == "pending"
    (run / "samples/extra.txt").write_text("SIMULATED unsealed file")
    assert style_review_status(run)["state"] == "invalidated"
    (run / "samples/extra.txt").unlink()
    local.write_bytes(b"SIMULATED corrupted font")
    assert style_review_status(run)["state"] == "invalidated"


def test_output_cannot_escape_run_or_replace_assets(sample_plan, tmp_path):
    run, plan = sample_plan
    for output in [tmp_path / "outside", run / "images", run / "nested/samples", run]:
        with pytest.raises(ValueError, match="immediate child"):
            samples.render_samples(run, plan, output)
