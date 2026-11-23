"""Audit an explicit file list, then produce an exact SHA-256 publication manifest.

This is a screening aid, not a human rights/privacy approval. Never discovers private
files recursively. Invoke with --files list.txt --output manifest.json.
"""

import argparse
import re
from pathlib import Path

from novel_quote_posters.storage import contained, digest_file, save_json

FORBIDDEN = {"data", "runs", "dist", "evidence", ".git", ".env", ".venv", ".state", ".ingest-staging", ".export-staging"}
PATTERNS = [r"[A-Za-z]:[/\\](?:Users|自动配图)", r"gh[oprs]_[A-Za-z0-9]{20,}",
            r"sk-[A-Za-z0-9_-]{20,}", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"]


def inspect(root: Path, names: list[str]) -> dict:
    rows, findings = [], []
    for name in names:
        path = contained(root, name)
        relative = path.relative_to(root.resolve()).as_posix()
        if set(Path(relative).parts) & FORBIDDEN or not path.is_file():
            raise ValueError(f"not a permitted public file: {relative}")
        if path.suffix.lower() in {".zip", ".png", ".jpg", ".epub", ".pdf"}:
            raise ValueError("binary/archives need separate metadata and content review")
        text = path.read_text(encoding="utf-8")
        matches = [pattern for pattern in PATTERNS if re.search(pattern, text)]
        if matches:
            findings.append({"path": relative, "patterns": matches})
        rows.append({"path": relative, "sha256": digest_file(path),
                     "category": "source_documentation_or_self_authored_fixture"})
    return {"schema_version": 1, "reviewer_type": "automated_screening",
            "human_privacy_review": "pending", "files": rows, "findings": findings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    names = [s.strip() for s in args.files.read_text(encoding="utf-8").splitlines() if s.strip()]
    report = inspect(Path.cwd(), names)
    save_json(args.output, report)
    if report["findings"]:
        raise SystemExit("public screening found issues; see report")
    print(f"Screened {len(report['files'])} explicitly listed files; human review remains pending.")


if __name__ == "__main__":
    main()
