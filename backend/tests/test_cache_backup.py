from __future__ import annotations

import importlib.util
from pathlib import Path
import sqlite3

import pytest


SCRIPT = Path(__file__).parents[2] / "deploy" / "cache_backup.py"
SPEC = importlib.util.spec_from_file_location("cache_backup", SCRIPT)
assert SPEC and SPEC.loader
cache_backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cache_backup)


def test_consistent_cache_snapshot_covers_all_sqlite_and_restores_to_empty_target(tmp_path):
    root = tmp_path / "otomo"
    nested = root / "cache" / "nested"
    nested.mkdir(parents=True)
    database = nested / "workspace.sqlite3"
    with sqlite3.connect(database) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE entries(id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO entries(value) VALUES('kept')")
    auth_key = root / "cache" / "auth" / "fernet.key"
    auth_key.parent.mkdir()
    auth_key.write_text("secret-key", encoding="utf-8")

    snapshot = tmp_path / "snapshot"
    manifest = cache_backup.create_snapshot(root, snapshot)

    assert manifest["database_count"] == 1
    assert (snapshot / "cache" / "nested" / "workspace.sqlite3").is_file()
    assert (snapshot / "cache" / "auth" / "fernet.key").read_text(encoding="utf-8") == "secret-key"
    assert not list(snapshot.rglob("*-wal"))
    assert cache_backup.verify_snapshot(snapshot)["format"] == "otomo-cache-snapshot-v1"

    restored = tmp_path / "restored"
    cache_backup.restore_drill(snapshot, restored)
    with sqlite3.connect(restored / "cache" / "nested" / "workspace.sqlite3") as conn:
        assert conn.execute("SELECT value FROM entries").fetchone()[0] == "kept"


def test_snapshot_verification_rejects_unlisted_files(tmp_path):
    root = tmp_path / "otomo"
    cache = root / "cache"
    cache.mkdir(parents=True)
    (cache / "auth.key").write_text("kept", encoding="utf-8")
    snapshot = tmp_path / "snapshot"
    cache_backup.create_snapshot(root, snapshot)

    (snapshot / "cache" / "injected.txt").write_text("not in manifest", encoding="utf-8")

    with pytest.raises(RuntimeError, match="do not match manifest"):
        cache_backup.verify_snapshot(snapshot)
