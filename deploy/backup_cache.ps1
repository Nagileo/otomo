param(
  [string]$Root = $(Resolve-Path "$PSScriptRoot\.."),
  [string]$Destination = $env:OTOMO_BACKUP_DEST
)

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$out = Join-Path $env:TEMP "otomo-cache-$stamp.zip"
$cache = Join-Path $Root "cache"
$tempRoot = [IO.Path]::GetFullPath($env:TEMP).TrimEnd([IO.Path]::DirectorySeparatorChar)
$snapshot = [IO.Path]::GetFullPath((Join-Path $tempRoot "otomo-cache-snapshot-$stamp-$PID"))
if (!$snapshot.StartsWith("$tempRoot$([IO.Path]::DirectorySeparatorChar)", [StringComparison]::OrdinalIgnoreCase)) {
  throw "snapshot path escaped the temporary directory: $snapshot"
}

if (!(Test-Path -LiteralPath $cache)) {
  throw "cache directory not found: $cache"
}

New-Item -ItemType Directory -Path $snapshot -ErrorAction Stop | Out-Null
try {
  $python = Get-Command python -ErrorAction SilentlyContinue
  if (!$python) { $python = Get-Command py -ErrorAction SilentlyContinue }
  if (!$python) { throw "Python 3 is required for consistent SQLite backup" }
  & $python.Source (Join-Path $Root "deploy\cache_backup.py") create --root $Root --output $snapshot
  if ($LASTEXITCODE -ne 0) { throw "consistent cache snapshot failed" }
  Compress-Archive -Path (Join-Path $snapshot "*") -DestinationPath $out -Force
} finally {
  if (Test-Path -LiteralPath $snapshot) { Remove-Item -LiteralPath $snapshot -Recurse -Force }
}
Write-Host "created $out"

if ($Destination) {
  $ossutil = Get-Command ossutil -ErrorAction SilentlyContinue
  if (!$ossutil) {
    throw "ossutil not found; install ossutil before setting OTOMO_BACKUP_DEST"
  }
  & ossutil cp $out "$Destination/$(Split-Path $out -Leaf)"
  Write-Host "uploaded to $Destination"
}
