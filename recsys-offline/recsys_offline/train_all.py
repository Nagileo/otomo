"""Train and atomically publish every available Bangumi media CF model.

Raw public user-item rows stay under recsys-offline/data (gitignored). Only the
aggregated item-item artifacts are published to the backend image.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

MEDIA = ("anime", "book", "music", "game", "real")
MEDIA_THRESHOLDS = {
    "anime": (5, 5),
    "book": (5, 4),
    "game": (4, 4),
    "music": (3, 3),
    "real": (4, 4),
}


def _published_models(publish_dir: Path) -> dict[str, object]:
    """Rebuild manifest state from actual artifacts so incremental runs are lossless."""
    models: dict[str, object] = {}
    for media in MEDIA:
        path = publish_dir / f"i2i_{media}.json"
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("items") and payload.get("meta"):
            models[media] = payload["meta"]
    return models


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data/bangumi")
    parser.add_argument("--publish-dir", default="../backend/otomo/data")
    parser.add_argument("--media", nargs="*", choices=MEDIA, default=list(MEDIA))
    parser.add_argument("--min-user", type=int, default=0,
                        help="全媒介统一阈值；0 表示使用各媒介默认值")
    parser.add_argument("--min-item", type=int, default=0,
                        help="全媒介统一阈值；0 表示使用各媒介默认值")
    parser.add_argument("--topk", type=int, default=50)
    parser.add_argument("--model", choices=["auto", "bm25", "als"], default="auto")
    parser.add_argument("--split", choices=["temporal", "random"], default="temporal")
    parser.add_argument("--min-relative-lift", type=float, default=0.01)
    parser.add_argument("--half-life-days", type=float, default=730.0)
    parser.add_argument("--time-floor", type=float, default=0.55)
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    publish_dir = Path(args.publish_dir).resolve()
    publish_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {
        "published_at": datetime.now(timezone.utc).isoformat(),
        "models": _published_models(publish_dir),
        "skipped": {},
    }
    hard_failures = 0
    with tempfile.TemporaryDirectory(prefix="otomo-cf-") as tmp:
        for media in args.media:
            source = data_dir / f"collections_{media}.csv"
            if not source.exists():
                manifest["skipped"][media] = "collection CSV not found"  # type: ignore[index]
                continue
            target_tmp = Path(tmp) / f"i2i_{media}.json"
            default_min_user, default_min_item = MEDIA_THRESHOLDS[media]
            min_user = args.min_user or default_min_user
            min_item = args.min_item or default_min_item
            command = [
                sys.executable, "-m", "recsys_offline.run_bangumi_cf",
                "--data", str(source), "--out", str(target_tmp),
                "--min-user", str(min_user), "--min-item", str(min_item),
                "--topk", str(args.topk), "--export-model", args.model,
                "--split", args.split,
                "--min-relative-lift", str(args.min_relative_lift),
                "--half-life-days", str(args.half_life_days),
                "--time-floor", str(args.time_floor),
            ]
            completed = subprocess.run(command, check=False)
            if completed.returncode == 3:
                manifest["skipped"][media] = "best CF model did not beat popularity quality gate"  # type: ignore[index]
                continue
            if completed.returncode or not target_tmp.exists():
                manifest["skipped"][media] = f"training failed ({completed.returncode})"  # type: ignore[index]
                hard_failures += 1
                continue
            payload = json.loads(target_tmp.read_text(encoding="utf-8"))
            meta = payload.get("meta") or {}
            if (
                not payload.get("items")
                or not meta.get("n_users")
                or not meta.get("quality_gate_passed")
            ):
                manifest["skipped"][media] = "artifact validation failed"  # type: ignore[index]
                hard_failures += 1
                continue
            destination = publish_dir / target_tmp.name
            stage = destination.with_suffix(".json.new")
            stage.write_bytes(target_tmp.read_bytes())
            os.replace(stage, destination)
            manifest["models"][media] = payload["meta"]  # type: ignore[index]
            print(f"published {media}: {destination}")
    manifest_path = publish_dir / "cf_models.json"
    stage_manifest = manifest_path.with_suffix(".json.new")
    stage_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(stage_manifest, manifest_path)
    print(f"manifest: {manifest_path}")
    return 1 if hard_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
