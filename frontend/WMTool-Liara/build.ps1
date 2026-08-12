param(
    [string]$MsysRoot = "C:\msys64",
    [string]$BuildDir = "builddir",
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$msysMinGW = Join-Path $MsysRoot "mingw64\bin"
if (-not (Test-Path (Join-Path $msysMinGW "pkg-config.exe"))) {
    throw "MSYS2 mingw64 pkg-config not found at $msysMinGW. Run: C:\msys64\usr\bin\pacman.exe -Sy mingw-w64-x86_64-gtk4 mingw-w64-x86_64-meson mingw-w64-x86_64-ninja"
}

$env:PATH = "$msysMinGW;" + $env:PATH
$env:PKG_CONFIG_PATH = Join-Path $MsysRoot "mingw64\lib\pkgconfig"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

if ($Clean -and (Test-Path $BuildDir)) {
    Remove-Item -Recurse -Force $BuildDir
}

if (-not (Test-Path $BuildDir)) {
    Write-Host "Configuring build..."
    & "$msysMinGW\meson.exe" setup $BuildDir
} else {
    Write-Host "Reconfiguring build..."
    & "$msysMinGW\meson.exe" setup --reconfigure $BuildDir
}

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Compiling..."
& "$msysMinGW\meson.exe" compile -C $BuildDir
