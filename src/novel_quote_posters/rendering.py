"""Incremental, recoverable composition. This module never calls an image service."""

import importlib.metadata
import re
from pathlib import Path

from . import layout
from .documents import commit_documents, recover_documents
from .images import validate_character_reference, validate_image_asset
from .quotes import check_display
from .source import validate_source
from .storage import contained, digest_file, fingerprint, load_json, save_json
from .workflow import Ledger


def render_inputs(run: Path, book: dict, quote: dict, character: dict, card: dict,
                  font: Path, width: int, height: int, draft: bool,
                  *, source_checked: bool = False) -> dict:
    check_display(book, quote, allow_rewrite=True)
    if (quote["speaker"]["character_id"] != card["character_id"]
            or quote["speaker"]["status"] not in {"explicit", "context_inferred"}
            or quote["quote_type"] not in {"dialogue", "monologue"}):
        raise ValueError("card speaker changed or is uncertain; reconcile the character and imagery before rendering")
    if not source_checked:
        validate_source(run, book)
    image_asset = validate_image_asset(run, card)
    reference_asset = validate_character_reference(run, character)
    references = {}
    if character.get("reference_image"):
        reference = contained(run, character["reference_image"])
        references[character["reference_image"]] = digest_file(reference)
    return {"renderer_version": 2, "renderer_code": {
                "layout": digest_file(Path(layout.__file__)), "rendering": digest_file(Path(__file__))},
            "packages": {p: importlib.metadata.version(p) for p in ["Pillow", "fonttools"]},
            "source": {k: book[k] for k in ["source_hash", "canonical_hash", "title", "parser_version"]},
            "quote": {k: v for k, v in quote.items() if k != "review"},
            "character": character, "references": references,
            "image_asset": image_asset, "reference_asset": reference_asset,
            "card": {k: v for k, v in card.items() if k not in {"poster", "review"}},
            "background_sha256": digest_file(contained(run, card["background"])),
            "font_sha256": digest_file(font), "width": width, "height": height, "draft": draft}


def validate_tracked_render(run: Path, card: dict, quote: dict, book: dict, character: dict,
                            report: dict, *, required: bool = False, source_checked: bool = False):
    options = report.get("render_options")
    if not options:
        if required:
            raise ValueError("final candidate requires tracked render inputs; run render again")
        return
    current = render_inputs(run, book, quote, character, card, Path(options["font"]),
                            options["width"], options["height"], options["draft"],
                            source_checked=source_checked)
    if report.get("input_fingerprint") != fingerprint(current):
        raise ValueError("render inputs changed; recompose and re-review")


def _valid_output(output: Path, signature: str) -> bool:
    try:
        report = load_json(output.with_suffix(".layout.json"))
        preview = output.with_name(output.stem + "-preview.png")
        return (report.get("input_fingerprint") == signature
                and report["output_sha256"] == digest_file(output)
                and report.get("preview_sha256") == digest_file(preview))
    except (ValueError, OSError, KeyError):
        return False


def render_cards(run: Path, font: Path, *, card_id: str | None = None, width: int = 1080,
                 height: int = 1440, draft: bool = True) -> dict:
    recover_documents(run)
    book = load_json(run / "book.json")
    validate_source(run, book)
    quotes = {q["quote_id"]: q for q in load_json(run / "quotes.json")}
    characters = load_json(run / "characters.json")
    cards = load_json(run / "cards.json")
    if len({c["card_id"] for c in cards}) != len(cards):
        raise ValueError("duplicate card IDs")
    selected = [c for c in cards if card_id is None or c["card_id"] == card_id]
    if not selected:
        raise ValueError("no matching cards")
    results = []
    with Ledger(run / ".state" / "ledger.sqlite") as ledger:
        for card in selected:
            identity = card["card_id"]
            if not re.fullmatch(r"[A-Za-z0-9_-]+", identity):
                raise ValueError("card ID must be an ASCII identifier")
            quote = quotes[card["quote_id"]]
            character = characters[card["character_id"]]
            inputs = render_inputs(run, book, quote, character, card, font, width, height, draft,
                                   source_checked=True)
            signature = fingerprint(inputs)
            snapshot = run / ".state" / "render-inputs" / f"{signature}.json"
            if not snapshot.exists():
                save_json(snapshot, inputs)
            # These explicit input nodes let a profile/quote/font change invalidate only dependents.
            parents = []
            for key in ["source", "quote", "character", "references", "card", "packages"]:
                value = inputs[key]
                blob = run / ".state" / "inputs" / f"{fingerprint(value)}.json"
                if not blob.exists():
                    save_json(blob, value)
                scope = (card["quote_id"] if key == "quote" else card["character_id"]
                         if key in {"character", "references"} else identity if key == "card" else "run")
                node = f"input:{key}:{scope}"
                ledger.put(node, blob)
                parents.append(node)
            for kind, path in [("source-file", contained(run, "source." + book["format"])),
                               ("font", font), ("background", contained(run, card["background"]))]:
                node = f"input:{kind}:{identity if kind in {'font', 'background'} else 'run'}"
                ledger.put(node, path)
                parents.append(node)
            node = f"input:render-options:{identity}"
            ledger.put(node, snapshot, parents)
            # Recover an interrupted card update by finding its already-completed content version.
            output = run / "posters" / f"{identity}-{signature[:16]}.png"
            attempt = 1
            while output.exists() and not _valid_output(output, signature):
                attempt += 1
                output = run / "posters" / f"{identity}-{signature[:16]}-attempt{attempt}.png"
            reused = _valid_output(output, signature)
            if not reused:
                report = layout.compose(contained(run, card["background"]), output,
                                        text=quote["display_text"], character=character["name"],
                                        title=book["title"], chapter=quote["source"]["chapter"],
                                        font_path=font, style_id=card["style_id"], width=width,
                                        height=height, draft=draft,
                                        rewrite=quote["text_mode"] == "rewrite",
                                        overrides=card.get("layout_overrides"))
                preview = output.with_name(output.stem + "-preview.png")
                report.update(input_fingerprint=signature, preview_sha256=digest_file(preview),
                              render_options={"font": str(font.resolve()), "width": width,
                                              "height": height, "draft": draft})
                save_json(output.with_suffix(".layout.json"), report)
            report_path = output.with_suffix(".layout.json")
            ledger.put(f"layout:{identity}", report_path, [node])
            ledger.put(f"poster:{identity}", output, [f"layout:{identity}"])
            preview = output.with_name(output.stem + "-preview.png")
            ledger.put(f"preview:{identity}", preview, [f"poster:{identity}"])
            relative = output.relative_to(run).as_posix()
            changed = card.get("poster") != relative
            if changed:
                card["poster"] = relative
                card["review"] = {"status": "pending", "reason": "render_version_changed"}
                commit_documents(run, {"cards.json": cards}, reason="render_card_completed")
            results.append({"card_id": identity, "poster": relative, "reused": reused,
                            "input_fingerprint": signature})
    return {"cards": results, "rendered": sum(not r["reused"] for r in results),
            "reused": sum(r["reused"] for r in results), "image_tool_calls": 0}
