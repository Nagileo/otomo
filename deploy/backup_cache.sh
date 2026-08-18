#!/usr/bin/env bash
set -euo pipefail

ROOT="${OTOMO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
DEST="${OTOMO_BACKUP_DEST:-}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${TMPDIR:-/tmp}/otomo-cache-${STAMP}.tar.gz"
SNAPSHOT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/otomo-cache-snapshot-XXXXXX")"
trap 'rm -rf -- "$SNAPSHOT_DIR"' EXIT

python3 "$ROOT/deploy/cache_backup.py" create --root "$ROOT" --output "$SNAPSHOT_DIR"
tar -C "$SNAPSHOT_DIR" -czf "$OUT" cache manifest.json

echo "created $OUT"

if [[ -n "$DEST" ]]; then
  if ! command -v ossutil >/dev/null 2>&1; then
    echo "ossutil not found; set OTOMO_BACKUP_DEST only after installing ossutil" >&2
    exit 2
  fi
  ossutil cp "$OUT" "$DEST/$(basename "$OUT")"
  echo "uploaded to $DEST"
fi
