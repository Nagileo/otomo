#!/usr/bin/env bash
set -euo pipefail

ROOT="${OTOMO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
DEST="${OTOMO_BACKUP_DEST:-}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${TMPDIR:-/tmp}/otomo-cache-${STAMP}.tar.gz"

tar -C "$ROOT" -czf "$OUT" cache/ltm cache/auth cache/sessions.sqlite3 cache/sessions.sqlite3-wal cache/sessions.sqlite3-shm 2>/dev/null || \
tar -C "$ROOT" -czf "$OUT" cache

echo "created $OUT"

if [[ -n "$DEST" ]]; then
  if ! command -v ossutil >/dev/null 2>&1; then
    echo "ossutil not found; set OTOMO_BACKUP_DEST only after installing ossutil" >&2
    exit 2
  fi
  ossutil cp "$OUT" "$DEST/$(basename "$OUT")"
  echo "uploaded to $DEST"
fi
