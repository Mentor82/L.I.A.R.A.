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
$serverManagerExePath = Join-Path $buildPath "liara-server-manager.exe"
$launcherProjectPath = Join-Path $projectRoot "launcher-rust"
$launcherBuiltExePath = Join-Path $launcherProjectPath "target\release\liara-server-manager-launcher.exe"
$launcherDistExePath = Join-Path $distPath "run-liara-server-manager.exe"
$bashPath = Join-Path $MsysRoot "usr\bin\bash.exe"
$usrBin = Join-Path $MsysRoot "usr\bin"
$ucrtBin = Join-Path $MsysRoot "ucrt64\bin"
$schemaSource = Join-Path $MsysRoot "ucrt64\share\glib-2.0\schemas"
$gdbusExe = Join-Path $ucrtBin "gdbus.exe"
$gspawnHelperExe = Join-Path $ucrtBin "gspawn-win64-helper.exe"
$gspawnHelperConsoleExe = Join-Path $ucrtBin "gspawn-win64-helper-console.exe"
$msysRuntimeTools = @("bash.exe", "grep.exe", "cut.exe", "tr.exe", "head.exe", "ps.exe")

Get-Process liara-server-manager,gdbus -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500

if (-not (Test-Path $serverManagerExePath)) {
    throw "Built executable not found at $serverManagerExePath"
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

Copy-Item $serverManagerExePath -Destination (Join-Path $binPath "liara-server-manager.exe")
if (Test-Path $gdbusExe) {
    Copy-Item $gdbusExe -Destination (Join-Path $binPath "gdbus.exe")
}
if (Test-Path $gspawnHelperExe) {
    Copy-Item $gspawnHelperExe -Destination (Join-Path $binPath "gspawn-win64-helper.exe")
}
if (Test-Path $gspawnHelperConsoleExe) {
    Copy-Item $gspawnHelperConsoleExe -Destination (Join-Path $binPath "gspawn-win64-helper-console.exe")
}
foreach ($tool in $msysRuntimeTools) {
    $toolPath = Join-Path $usrBin $tool
    if (Test-Path $toolPath) {
        Copy-Item $toolPath -Destination (Join-Path $binPath $tool)
    } else {
        Write-Warning "MSYS runtime tool missing: $toolPath"
    }
}

$projectRootUnix = ($projectRoot -replace '\\', '/') -replace '^([A-Za-z]):', '/$1'
$lddTargets = @("$projectRootUnix/$BuildDir/liara-server-manager.exe")
foreach ($tool in $msysRuntimeTools) {
    $target = Join-Path $usrBin $tool
    if (Test-Path $target) {
        $targetUnix = ($target -replace '\\', '/') -replace '^([A-Za-z]):', '/$1'
        $lddTargets += $targetUnix
    }
}

$dllPaths = @()
foreach ($target in $lddTargets) {
    $lddCommand = "export PATH=/ucrt64/bin:/usr/bin:`$PATH; ldd $target"
    $lddOutput = & $bashPath -lc $lddCommand
    $dllPaths += $lddOutput |
        ForEach-Object {
            if ($_ -match '=> (/(ucrt64|usr)/bin/[^ ]+)') {
                $matches[1]
            }
        }
}
$dllPaths = $dllPaths | Sort-Object -Unique

foreach ($dllPath in $dllPaths) {
    $windowsPath = $dllPath
    $windowsPath = $windowsPath -replace '^/ucrt64/bin/', ($ucrtBin.Replace('\', '/') + '/')
    $windowsPath = $windowsPath -replace '^/usr/bin/', ($usrBin.Replace('\', '/') + '/')
    $windowsPath = $windowsPath -replace '/', '\'
    if (Test-Path $windowsPath) {
        Copy-Item $windowsPath -Destination $libPath
    }
}

Copy-Item (Join-Path $schemaSource "*") -Destination (Join-Path $configPath "glib-2.0\schemas") -Recurse

$launcherBuilt = $false
if (Test-Path (Join-Path $launcherProjectPath "Cargo.toml")) {
    $cargo = Get-Command cargo -ErrorAction SilentlyContinue
    if ($cargo -ne $null) {
        Push-Location $launcherProjectPath
        try {
            & $cargo.Source build --release
            if ($LASTEXITCODE -eq 0 -and (Test-Path $launcherBuiltExePath)) {
                Copy-Item $launcherBuiltExePath -Destination $launcherDistExePath
                $launcherBuilt = $true
            } else {
                Write-Warning "Rust launcher build did not produce executable; falling back to script launcher"
            }
        } finally {
            Pop-Location
        }
    } else {
        Write-Warning "cargo not found; falling back to script launcher"
    }
}

$serverLauncherPs1 = @'
$ErrorActionPreference = "Stop"
$appDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $appDir
$env:LIARA_PROJECT_ROOT = (Resolve-Path (Join-Path $appDir "..\..\..")).Path
$env:LIARA_SERVER_MANAGER_CONFIG = Join-Path $appDir "config\server-manager.json"
$env:LIARA_SERVER_MANAGER_LOG = Join-Path $appDir "logs\ui\server-manager.log"
$env:PATH = "$appDir\bin;$appDir\lib;$env:PATH"
$env:GTK_USE_PORTAL = "0"
if (-not (Test-Path (Join-Path $appDir "cache"))) { New-Item -ItemType Directory -Path (Join-Path $appDir "cache") | Out-Null }
if (-not (Test-Path (Join-Path $appDir "logs\ui"))) { New-Item -ItemType Directory -Path (Join-Path $appDir "logs\ui") -Force | Out-Null }
if (-not (Test-Path (Join-Path $appDir "logs\ui\server-manager.log"))) { Set-Content -Path (Join-Path $appDir "logs\ui\server-manager.log") -Value "" -Encoding ASCII }
$env:XDG_CACHE_HOME = Join-Path $appDir "cache"
$env:XDG_DATA_DIRS = Join-Path $appDir "config"
$env:GSETTINGS_SCHEMA_DIR = Join-Path $appDir "config\glib-2.0\schemas"
& (Join-Path $appDir "bin\liara-server-manager.exe")
'@

$serverLauncher = @'
@echo off
setlocal
set "APPDIR=%~dp0"
if exist "%APPDIR%run-liara-server-manager.exe" (
    "%APPDIR%run-liara-server-manager.exe"
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%APPDIR%run-liara-server-manager.ps1"
)
endlocal
'@

Set-Content -Path (Join-Path $distPath "run-liara-server-manager.ps1") -Value $serverLauncherPs1 -Encoding ASCII
Set-Content -Path (Join-Path $distPath "run-liara-server-manager.cmd") -Value $serverLauncher -Encoding ASCII
Set-Content -Path (Join-Path $configPath "server-manager.json") -Value "{`n  `"autostart`": false,`n  `"restart_on_nonzero`": false,`n  `"start_delay_ms`": 1500,`n  `"env_file`": `"C:\\ai\\.env`",`n  `"log_level`": `"INFO`"`n}" -Encoding ASCII
Set-Content -Path (Join-Path $uiLogsPath "server-manager.log") -Value "" -Encoding ASCII

Write-Host "Packaged Server Manager to $distPath"
if ($launcherBuilt) {
    Write-Host "Rust launcher created: $launcherDistExePath"
} else {
    Write-Host "Rust launcher not available, script launcher remains active"
}
