"""Checkpointed initial ingest and atomic publication of a complete run directory."""

import os
import uuid
from pathlib import Path

from .ingest import MAX_INPUT, PARSER_VERSION, chunks, ingest
from .quotes import candidates
from .storage import copy_atomic, digest_file, load_json, save_json, sha256
from .workflow import Ledger


def _signature(value) -> str:
    import json
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode())


def initialize_run(source: Path, run: Path, *, title: str | None = None,
                   encoding: str | None = None, size: int = 6000, overlap: int = 300,
                   resume: bool = False, image_limit: int = 10) -> dict:
    source, run = source.resolve(), run.resolve()
    if source.suffix.lower() not in {".txt", ".epub"}:
        raise ValueError("V1 currently supports TXT and EPUB only; DOCX/PDF/OCR not implemented")
    if source.stat().st_size > MAX_INPUT:
        raise ValueError("input exceeds 100 MiB limit")
    if not 0 <= overlap < size or image_limit <= 0:
        raise ValueError("invalid chunk window or image budget")
    if run == run.parent or not run.name:
        raise ValueError("a named run directory is required")
    contract = {"schema_version": 1, "source_hash": digest_file(source),
                "format": source.suffix.lower()[1:], "title": title or source.stem,
                "encoding": encoding, "chunk_size": size, "overlap": overlap,
                "parser_version": PARSER_VERSION, "parser_code": digest_file(Path(ingest.__code__.co_filename)),
                "candidate_code": digest_file(Path(candidates.__code__.co_filename)),
                "bootstrap_code": digest_file(Path(__file__)),
                "image_limit": image_limit}
    signature = _signature(contract)
    # A cooperating process holds an OS-released SQLite writer lock, not a stale PID file.
    staging_root = (run.parent / ".ingest-staging").resolve()
    staging_root.mkdir(parents=True, exist_ok=True)
    stage = (staging_root / f"{run.name}-{signature[:20]}").resolve()
    if stage.parent != staging_root or run.parent != staging_root.parent:
        raise ValueError("resolved ingest paths escape the requested parent directory")
    with Ledger(staging_root / "coordination.sqlite") as lock, lock.db:
        lock.db.execute("UPDATE config SET value=value WHERE key='limit'")
        if run.exists():
            if not resume:
                raise ValueError("run already exists; use --resume with identical inputs or a new run ID")
            seal = load_json(run / "ingest-seal.json")
            if load_json(run / "ingest-contract.json") != contract:
                raise ValueError("resume input/configuration changed; choose a new run ID")
            for name, expected in seal["immutable_files"].items():
                if digest_file(run / name) != expected:
                    raise ValueError(f"completed ingest asset damaged: {name}; preserve run and reimport")
            from .documents import recover_documents
            recover_documents(run)
            return {**load_json(run / "ingest-report.json"), "reused": True,
                    "reused_phases": ["published_run"]}
        if stage.exists() and not resume:
            raise ValueError("unfinished ingest checkpoint exists; use --resume")
        stage.mkdir(exist_ok=True)
        checkpoint_path = stage / "checkpoint.json"
        checkpoint = load_json(checkpoint_path) if checkpoint_path.exists() else {
            "contract": contract, "outputs": {}}
        if checkpoint["contract"] != contract:
            raise ValueError("checkpoint contract changed")
        original = stage / ("source." + contract["format"])
        reused = []

        def quarantine(path: Path):
            if path.exists():
                # File-only rename; both checked paths remain inside this exact staging folder.
                target = stage / f"damaged-{uuid.uuid4().hex}-{path.name}"
                if path.resolve().parent != stage or target.resolve().parent != stage:
                    raise ValueError("unsafe quarantine path")
                path.rename(target)

        if not original.exists() or digest_file(original) != contract["source_hash"]:
            quarantine(original)
            copy_atomic(source, original)
        if digest_file(original) != contract["source_hash"]:
            raise ValueError("source changed while copying; restart with the current input")
        save_json(checkpoint_path, checkpoint)

        def phase(name: str, produce):
            path = stage / name
            if (path.exists() and checkpoint["outputs"].get(name) == digest_file(path)):
                reused.append(name)
                return load_json(path)
            quarantine(path)
            value = produce()
            save_json(path, value)
            checkpoint["outputs"][name] = digest_file(path)
            save_json(checkpoint_path, checkpoint)
            return value

        book = phase("book.json", lambda: ingest(original, contract["title"], encoding))
        windows = phase("chunks.json", lambda: chunks(book, size, overlap))
        extracted = phase("candidate-extraction.json", lambda: dict(zip(
            ["quotes", "issues"], candidates(book))))
        phase("quotes.json", lambda: extracted["quotes"])
        report = {"source_hash": book["source_hash"], "chapters_indexed": len(book["chapters"]),
                  "characters_indexed": len(book["text"]), "chunks_indexed": len(windows),
                  "semantic_chunks_processed": 0, "candidates": len(extracted["quotes"]),
                  "issues": extracted["issues"], "speaker_review": "pending",
                  "coverage_claim": "index_only"}
        save_json(stage / "ingest-report.json", report)
        save_json(stage / "ingest-contract.json", contract)
        with Ledger(stage / ".state" / "ledger.sqlite", limit=image_limit):
            pass
        seal_files = [original.name, "book.json", "ingest-report.json", "ingest-contract.json"]
        save_json(stage / "ingest-seal.json", {"schema_version": 1, "signature": signature,
                  "immutable_files": {name: digest_file(stage / name) for name in seal_files}})
        # All files are complete and connections closed before renaming on Windows.
        # Verify both absolute paths once more immediately before directory publication.
        if stage.resolve().parent != staging_root or run.resolve().parent != staging_root.parent:
            raise ValueError("unsafe ingest publication path")
        if run.exists():
            raise ValueError("run appeared during ingest; no destination was overwritten")
        os.rename(stage, run)
        return {**report, "reused": False, "reused_phases": reused}
