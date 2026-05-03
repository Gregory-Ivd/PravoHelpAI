from __future__ import annotations

import os
import time

from pravohelp.document import generator


def test_cleanup_removes_old_files(tmp_path, monkeypatch):
    fake_output = tmp_path / "output"
    fake_output.mkdir()
    user_dir = fake_output / "999"
    user_dir.mkdir()

    old_file = user_dir / "old.docx"
    old_file.write_bytes(b"old")
    fresh_file = user_dir / "fresh.docx"
    fresh_file.write_bytes(b"fresh")

    old_time = time.time() - 7200
    os.utime(old_file, (old_time, old_time))

    monkeypatch.setattr(generator, "OUTPUT_DIR", fake_output)

    removed = generator.cleanup_old_documents(ttl_seconds=3600)

    assert removed == 1
    assert not old_file.exists()
    assert fresh_file.exists()


def test_cleanup_handles_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(generator, "OUTPUT_DIR", tmp_path / "does-not-exist")
    assert generator.cleanup_old_documents() == 0
