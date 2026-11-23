import hashlib

import pytest

from scripts.public_manifest import inspect


def test_public_hash_screening_rejects_windows_newlines_before_git_changes_bytes(tmp_path):
    (tmp_path / "example.md").write_bytes("项目记录\r\n下一行\r\n".encode())
    with pytest.raises(ValueError, match="LF before hashing"):
        inspect(tmp_path, ["example.md"])


def test_public_hash_matches_canonical_utf8_lf_bytes(tmp_path):
    raw = "项目记录\n下一行\n".encode()
    (tmp_path / "example.md").write_bytes(raw)
    report = inspect(tmp_path, ["example.md"])
    assert report["files"][0]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert report["findings"] == [] and report["human_privacy_review"] == "pending"
