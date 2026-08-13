#!/usr/bin/env bash
set -euo pipefail

START="${START:-1}"
END="${END:-20000}"
if [[ $# -gt 0 ]]; then
  MEDIA=("$@")
else
  MEDIA=(anime book music game real)
fi
for kind in "${MEDIA[@]}"; do
  python -m recsys_offline.bangumi_collect --start "$START" --end "$END" --stype "$kind" --outdir data/bangumi
done
python -m recsys_offline.train_all --data-dir data/bangumi --publish-dir ../backend/otomo/data --media "${MEDIA[@]}"
