import hashlib

import pytest

from novel_quote_posters.storage import copy_atomic, digest_file


def test_atomic_copy_closes_and_cleans_temporary_file_when_source_is_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        copy_atomic(tmp_path / "missing", tmp_path / "destination")
    assert not (tmp_path / "destination").exists()
    assert not list(tmp_path.glob(".copying-*"))


def test_streaming_hash_matches_exact_copied_bytes(tmp_path):
    raw = b"source data" * 200000
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_bytes(raw)
    copy_atomic(source, target)
    assert digest_file(target) == hashlib.sha256(raw).hexdigest()
    assert target.read_bytes() == source.read_bytes()
