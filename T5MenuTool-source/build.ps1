param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Dist = Join-Path $Root "dist"
$Build = Join-Path $Root "build"
$Spec = Join-Path $Root "T5MenuTool.spec"

if ($Clean) {
    if (Test-Path -LiteralPath $Dist) { Remove-Item -LiteralPath $Dist -Recurse -Force }
    if (Test-Path -LiteralPath $Build) { Remove-Item -LiteralPath $Build -Recurse -Force }
}

python -m pip install pyinstaller tkinterdnd2 -r (Join-Path $Root "requirements.txt")

python -m PyInstaller $Spec --noconfirm --distpath $Dist --workpath $Build

$Exe = Join-Path $Dist "T5MenuTool.exe"
if (-not (Test-Path -LiteralPath $Exe)) {
    throw "Build finished but executable was not found: $Exe"
}

$Zip = Join-Path $Root "T5MenuTool-win-x64.zip"
if (Test-Path -LiteralPath $Zip) { Remove-Item -LiteralPath $Zip -Force }
Compress-Archive -LiteralPath $Exe -DestinationPath $Zip

Write-Host ""
Write-Host "Built:    $Exe"
Write-Host "Packaged: $Zip"
