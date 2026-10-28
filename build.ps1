<#
    Build RedScan into a single self-contained Windows executable.

    Usage:  ./build.ps1            # build dist/redscan.exe
            ./build.ps1 -Clean     # remove build artefacts first
            ./build.ps1 -Icon      # regenerate the radar icon too

    Keep this file ASCII: Windows PowerShell 5.1 reads BOM-less scripts as
    ANSI, and UTF-8 box drawing characters decode into stray quote marks.
#>
param(
    [switch]$Clean,
    [switch]$Icon
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host ""
Write-Host "  RedScan build" -ForegroundColor Red
Write-Host "  -------------" -ForegroundColor DarkRed

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
    if ($LASTEXITCODE -ne 0) { throw "make_icon.py failed" }
}

# Windows version resource, so Explorer's Details tab shows the real version.
$version = (python -c "from redscan import __version__; print(__version__)").Trim()
if ($LASTEXITCODE -ne 0 -or -not $version) { throw "could not read redscan.__version__" }
$parts = @($version.Split(".") | ForEach-Object { [int]$_ })
while ($parts.Count -lt 4) { $parts += 0 }
$tuple = $parts[0..3] -join ", "

New-Item -ItemType Directory -Force build | Out-Null
$versionFile = Join-Path $PSScriptRoot "build\version_info.txt"
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($tuple), prodvers=($tuple), mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'gavingimmestad'),
      StringStruct('FileDescription', 'RedScan - port scanner and IP pinger'),
      StringStruct('FileVersion', '$version'),
      StringStruct('InternalName', 'redscan'),
      StringStruct('LegalCopyright', 'Copyright (c) 2026 Gavin Gimmestad. MIT License.'),
      StringStruct('OriginalFilename', 'redscan.exe'),
      StringStruct('ProductName', 'RedScan'),
      StringStruct('ProductVersion', '$version')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@ | Set-Content -Path $versionFile -Encoding ASCII

$pyinstaller = @(
    "--onefile"
    "--name", "redscan"
    "--console"
    "--icon", "assets/redscan.ico"
    "--version-file", $versionFile
    "--collect-all", "rich"
    # pygments' image formatter drags in Pillow (and numpy) when they are installed
    "--exclude-module", "PIL"
    "--noconfirm"
    "--clean"
    "run_redscan.py"
)

Write-Host "  running pyinstaller (version $version)" -ForegroundColor DarkGray
python -m PyInstaller @pyinstaller
if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed with exit code $LASTEXITCODE" }

$exe = Join-Path $PSScriptRoot "dist\redscan.exe"
if (-not (Test-Path $exe)) {
    Write-Host "  build failed: no executable produced" -ForegroundColor Red
    exit 1
}

$size = [math]::Round((Get-Item $exe).Length / 1MB, 2)
Write-Host ""
Write-Host "  built dist\redscan.exe  ($size MB)" -ForegroundColor Red
Write-Host ""
