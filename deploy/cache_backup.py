#!/usr/bin/env python3
"""Create and verify consistent Otomo cache snapshots.

SQLite files are copied with Connection.backup() while the service is live;
WAL/SHM files are never archived directly. Other cache files (including auth
keys and non-SQLite memory data) are copied byte-for-byte with a hash manifest.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys


SQLITE_SUFFIXES = {".sqlite3", ".sqlite", ".db"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _integrity_check(path: Path) -> None:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=30) as conn:
        result = conn.execute("PRAGMA integrity_check").fetchone()
    if not result or result[0] != "ok":
        raise RuntimeError(f"SQLite integrity_check failed: {path}: {result}")


def _is_sqlite(path: Path) -> bool:
    return path.suffix.lower() in SQLITE_SUFFIXES


def create_snapshot(root: Path, output: Path) -> dict:
    root = root.resolve()
    cache = root / "cache"
    output = output.resolve()
    if not cache.is_dir():
        raise FileNotFoundError(f"cache directory not found: {cache}")
    if output == cache or cache in output.parents:
        raise ValueError(f"snapshot output must be outside the live cache: {output}")
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"snapshot output must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    snapshot_cache = output / "cache"
    snapshot_cache.mkdir()

    database_paths = {path.resolve() for path in cache.rglob("*") if path.is_file() and _is_sqlite(path)}
    for source in sorted(database_paths):
        relative = source.relative_to(cache)
        destination = snapshot_cache / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(source, timeout=30) as source_conn:
            with sqlite3.connect(destination) as destination_conn:
                source_conn.backup(destination_conn)
                # The journal mode lives in the database header and can be WAL
                # even though the snapshot itself is complete. Convert the
                # standalone copy to DELETE mode so verification/restoration
                # never depends on a sidecar file.
                destination_conn.execute("PRAGMA journal_mode=DELETE").fetchone()
        _integrity_check(destination)

    for source in sorted(path for path in cache.rglob("*") if path.is_file()):
        if source.resolve() in database_paths or source.name.endswith(("-wal", "-shm", "-journal")):
            continue
        relative = source.relative_to(cache)
        destination = snapshot_cache / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    files = []
    for path in sorted(path for path in snapshot_cache.rglob("*") if path.is_file()):
        files.append({
            "path": path.relative_to(output).as_posix(),
            "size": path.stat().st_size,
            "sha256": _sha256(path),
            "sqlite": _is_sqlite(path),
        })
    manifest = {
        "format": "otomo-cache-snapshot-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
        "database_count": sum(1 for item in files if item["sqlite"]),
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    verify_snapshot(output)
    return manifest


def verify_snapshot(snapshot: Path) -> dict:
    snapshot = snapshot.resolve()
    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "otomo-cache-snapshot-v1":
        raise RuntimeError("unsupported or missing snapshot format")
    declared_paths: set[str] = set()
    for item in manifest.get("files") or []:
        relative = Path(str(item["path"]))
        if not relative.parts or relative.parts[0] != "cache":
            raise RuntimeError(f"snapshot file is outside cache: {item['path']}")
        canonical = relative.as_posix()
        if canonical in declared_paths:
            raise RuntimeError(f"duplicate snapshot manifest entry: {canonical}")
        declared_paths.add(canonical)
        path = (snapshot / relative).resolve()
        if snapshot not in path.parents or not path.is_file():
            raise RuntimeError(f"snapshot file missing or escaped root: {item['path']}")
        if path.stat().st_size != int(item["size"]) or _sha256(path) != item["sha256"]:
            raise RuntimeError(f"snapshot hash mismatch: {item['path']}")
        if item.get("sqlite"):
            _integrity_check(path)
    actual_paths = {
        path.relative_to(snapshot).as_posix()
        for path in (snapshot / "cache").rglob("*")
        if path.is_file()
    }
    if actual_paths != declared_paths:
        missing = sorted(declared_paths - actual_paths)
        unexpected = sorted(actual_paths - declared_paths)
        raise RuntimeError(
            f"snapshot contents do not match manifest; missing={missing}, unexpected={unexpected}"
        )
    return manifest


def restore_drill(snapshot: Path, target: Path) -> dict:
    """Restore only into a new/empty directory, suitable for a recovery drill."""
    snapshot = snapshot.resolve()
    target = target.resolve()
    manifest = verify_snapshot(snapshot)
    if target == snapshot or snapshot in target.parents:
        raise ValueError(f"restore target must be outside the snapshot: {target}")
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"restore target must be empty: {target}")
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(snapshot / "cache", target / "cache", dirs_exist_ok=True)
    for item in manifest.get("files") or []:
        restored = target / str(item["path"])
        if _sha256(restored) != item["sha256"]:
            raise RuntimeError(f"restored file hash mismatch: {item['path']}")
        if item.get("sqlite"):
            _integrity_check(restored)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--root", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--snapshot", type=Path, required=True)
    restore = subparsers.add_parser("restore-drill")
    restore.add_argument("--snapshot", type=Path, required=True)
    restore.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "create":
            result = create_snapshot(args.root, args.output)
        elif args.command == "verify":
            result = verify_snapshot(args.snapshot)
        else:
            result = restore_drill(args.snapshot, args.target)
    except Exception as exc:  # noqa: BLE001 - CLI must fail closed with a useful message
        print(f"backup error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "ok": True,
        "files": len(result.get("files") or []),
        "databases": result.get("database_count", 0),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
