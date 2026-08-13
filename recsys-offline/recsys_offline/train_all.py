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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data/bangumi")
    parser.add_argument("--publish-dir", default="../backend/otomo/data")
    parser.add_argument("--media", nargs="*", choices=MEDIA, default=list(MEDIA))
    parser.add_argument("--min-user", type=int, default=5)
    parser.add_argument("--min-item", type=int, default=5)
    parser.add_argument("--topk", type=int, default=50)
    parser.add_argument("--model", choices=["bm25", "als"], default="bm25")
    parser.add_argument("--split", choices=["temporal", "random"], default="temporal")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    publish_dir = Path(args.publish_dir).resolve()
    publish_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {
        "published_at": datetime.now(timezone.utc).isoformat(),
        "models": {},
        "skipped": {},
    }
    with tempfile.TemporaryDirectory(prefix="otomo-cf-") as tmp:
        for media in args.media:
            source = data_dir / f"collections_{media}.csv"
            if not source.exists():
                manifest["skipped"][media] = "collection CSV not found"  # type: ignore[index]
                continue
            target_tmp = Path(tmp) / f"i2i_{media}.json"
            command = [
                sys.executable, "-m", "recsys_offline.run_bangumi_cf",
                "--data", str(source), "--out", str(target_tmp),
                "--min-user", str(args.min_user), "--min-item", str(args.min_item),
                "--topk", str(args.topk), "--export-model", args.model,
                "--split", args.split,
            ]
            completed = subprocess.run(command, check=False)
            if completed.returncode or not target_tmp.exists():
                manifest["skipped"][media] = f"training failed ({completed.returncode})"  # type: ignore[index]
                continue
            payload = json.loads(target_tmp.read_text(encoding="utf-8"))
            if not payload.get("items") or not (payload.get("meta") or {}).get("n_users"):
                manifest["skipped"][media] = "artifact validation failed"  # type: ignore[index]
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


if __name__ == "__main__":
    main()
