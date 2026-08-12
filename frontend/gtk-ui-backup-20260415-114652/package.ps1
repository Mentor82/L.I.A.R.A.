param(
    [string]$MsysRoot = "C:\msys64",
    [string]$BuildDir = "builddir",
    [string]$DistDir = "dist"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$buildPath = Join-Path $projectRoot $BuildDir
$distPath = Join-Path $projectRoot $DistDir
$binPath = Join-Path $distPath "bin"
$libPath = Join-Path $distPath "lib"
$configPath = Join-Path $distPath "config"
$cachePath = Join-Path $distPath "cache"
$logsPath = Join-Path $distPath "logs"
$uiLogsPath = Join-Path $logsPath "ui"
$exePath = Join-Path $buildPath "liara-gtk-ui.exe"
$bashPath = Join-Path $MsysRoot "usr\bin\bash.exe"
$ucrtBin = Join-Path $MsysRoot "ucrt64\bin"
$schemaSource = Join-Path $MsysRoot "ucrt64\share\glib-2.0\schemas"
$stylePath = Join-Path $projectRoot "style.css"
$gdbusExe = Join-Path $ucrtBin "gdbus.exe"

if (-not (Test-Path $exePath)) {
    throw "Built executable not found at $exePath"
}

if (-not (Test-Path $bashPath)) {
    throw "MSYS2 bash not found at $bashPath"
}

if (Test-Path $distPath) {
    Remove-Item -Recurse -Force $distPath
}

New-Item -ItemType Directory -Path $distPath | Out-Null
New-Item -ItemType Directory -Path $binPath | Out-Null
New-Item -ItemType Directory -Path $libPath | Out-Null
New-Item -ItemType Directory -Path $cachePath | Out-Null
New-Item -ItemType Directory -Path $uiLogsPath -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $configPath "glib-2.0\schemas") -Force | Out-Null

Copy-Item $exePath -Destination (Join-Path $binPath "liara-gtk-ui.exe")
Copy-Item $stylePath -Destination (Join-Path $configPath "style.css")
if (Test-Path $gdbusExe) {
    Copy-Item $gdbusExe -Destination (Join-Path $binPath "gdbus.exe")
}

$lddCommand = "export PATH=/ucrt64/bin:/usr/bin:`$PATH; ldd /c/ai/LIARA/frontend/gtk-ui/$BuildDir/liara-gtk-ui.exe"
$lddOutput = & $bashPath -lc $lddCommand

$dllPaths = $lddOutput |
    ForEach-Object {
        if ($_ -match '=> (/ucrt64/bin/[^ ]+)') {
            $matches[1]
        }
    } |
    Sort-Object -Unique

foreach ($dllPath in $dllPaths) {
    $windowsPath = $dllPath -replace '^/ucrt64/bin/', ($ucrtBin.Replace('\', '/') + '/')
    $windowsPath = $windowsPath -replace '/', '\'
    Copy-Item $windowsPath -Destination $libPath
}

Copy-Item (Join-Path $schemaSource "*") -Destination (Join-Path $configPath "glib-2.0\schemas") -Recurse

$launcher = @'
@echo off
setlocal
set "APPDIR=%~dp0"
set "PATH=%APPDIR%bin;%APPDIR%lib;%PATH%"
set "GTK_USE_PORTAL=0"
if not exist "%APPDIR%cache" mkdir "%APPDIR%cache"
if not exist "%APPDIR%logs\ui" mkdir "%APPDIR%logs\ui"
set "XDG_CACHE_HOME=%APPDIR%cache"
set "XDG_DATA_DIRS=%APPDIR%config"
set "GSETTINGS_SCHEMA_DIR=%APPDIR%config\glib-2.0\schemas"
start "" "%APPDIR%bin\liara-gtk-ui.exe"
endlocal
'@

Set-Content -Path (Join-Path $distPath "run-liara-gtk-ui.cmd") -Value $launcher -Encoding ASCII

Write-Host "Packaged GTK UI to $distPath"
