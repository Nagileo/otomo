param(
  [string]$Root = $(Resolve-Path "$PSScriptRoot\.."),
  [string]$Destination = $env:OTOMO_BACKUP_DEST
)

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$out = Join-Path $env:TEMP "otomo-cache-$stamp.zip"
$cache = Join-Path $Root "cache"

if (!(Test-Path -LiteralPath $cache)) {
  throw "cache directory not found: $cache"
}

Compress-Archive -Path $cache -DestinationPath $out -Force
Write-Host "created $out"

if ($Destination) {
  $ossutil = Get-Command ossutil -ErrorAction SilentlyContinue
  if (!$ossutil) {
    throw "ossutil not found; install ossutil before setting OTOMO_BACKUP_DEST"
  }
  & ossutil cp $out "$Destination/$(Split-Path $out -Leaf)"
  Write-Host "uploaded to $Destination"
}
