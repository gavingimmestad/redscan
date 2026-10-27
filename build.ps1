<#
    Build RedScan into a single self-contained Windows executable.

    Usage:  ./build.ps1            # build dist/RedScan.exe
            ./build.ps1 -Clean     # remove build artefacts first
            ./build.ps1 -Icon      # regenerate the radar icon too
#>
param(
    [switch]$Clean,
    [switch]$Icon
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host ""
Write-Host "  RedScan build" -ForegroundColor Red
Write-Host "  ─────────────" -ForegroundColor DarkRed

if ($Clean) {
    Write-Host "  cleaning build/ dist/ *.spec" -ForegroundColor DarkGray
    foreach ($path in @("build", "dist")) {
        if (Test-Path $path) { Remove-Item $path -Recurse -Force }
    }
    Get-ChildItem -Filter "*.spec" -ErrorAction SilentlyContinue | Remove-Item -Force
}

if ($Icon) {
    Write-Host "  rendering assets/redscan.ico" -ForegroundColor DarkGray
    python tools/make_icon.py
}

$pyinstaller = @(
    "--onefile"
    "--name", "RedScan"
    "--console"
    "--icon", "assets/redscan.ico"
    "--collect-all", "rich"
    "--noconfirm"
    "--clean"
    "run_redscan.py"
)

Write-Host "  running pyinstaller" -ForegroundColor DarkGray
python -m PyInstaller @pyinstaller

$exe = Join-Path $PSScriptRoot "dist\RedScan.exe"
if (-not (Test-Path $exe)) {
    Write-Host "  build failed: no executable produced" -ForegroundColor Red
    exit 1
}

$size = [math]::Round((Get-Item $exe).Length / 1MB, 2)
Write-Host ""
Write-Host "  built dist\RedScan.exe  ($size MB)" -ForegroundColor Red
Write-Host ""
