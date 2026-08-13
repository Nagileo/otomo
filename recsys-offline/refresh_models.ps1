param(
  [int]$Start = 1,
  [int]$End = 20000,
  [string[]]$Media = @("anime", "book", "music", "game", "real"),
  [switch]$SkipCollect
)

$ErrorActionPreference = "Stop"
foreach ($kind in $Media) {
  if (-not $SkipCollect) {
    python -m recsys_offline.bangumi_collect --start $Start --end $End --stype $kind --outdir data/bangumi
    if ($LASTEXITCODE -ne 0) { throw "collection failed: $kind" }
  }
}
python -m recsys_offline.train_all --data-dir data/bangumi --publish-dir ../backend/otomo/data --media $Media
if ($LASTEXITCODE -ne 0) { throw "training/publish failed" }
