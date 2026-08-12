param(
    [string]$MsysRoot = "C:\msys64",
    [string]$BuildDir = "builddir",
    [string]$DistDir = "dist",
    [bool]$UseFallbackDistOnLock = $true
)

$ErrorActionPreference = "Stop"

function Stop-LiaraUiProcesses {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot,
        [Parameter(Mandatory = $true)]
        [string]$TargetExePath
    )

    $projectPrefix = $ProjectRoot.TrimEnd('\\') + '\\'

    Get-CimInstance Win32_Process -Filter "Name='liara-gtk-ui.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $exePath = $_.ExecutablePath
            if ([string]::IsNullOrWhiteSpace($exePath)) {
                return $false
            }

            [string]::Equals($exePath, $TargetExePath, [System.StringComparison]::OrdinalIgnoreCase) -or
            $exePath.StartsWith($projectPrefix, [System.StringComparison]::OrdinalIgnoreCase)
        } |
        ForEach-Object {
            try {
                Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop
            } catch {
            }
        }
}

function Remove-DistDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot,
        [int]$MaxAttempts = 3
    )

    if (-not (Test-Path $Path)) {
        return $true
    }

    $packagedExe = Join-Path $Path "bin\liara-gtk-ui.exe"
    Stop-LiaraUiProcesses -ProjectRoot $ProjectRoot -TargetExePath $packagedExe

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        try {
            Remove-Item -Recurse -Force $Path
            return (-not (Test-Path $Path))
        } catch {
            if ($attempt -lt $MaxAttempts) {
                # One more kill pass in case the process was still shutting down.
                Stop-LiaraUiProcesses -ProjectRoot $ProjectRoot -TargetExePath $packagedExe
                [System.Threading.Thread]::Sleep(250)
            }
        }
    }

    return (-not (Test-Path $Path))
}

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
$mingwBin = Join-Path $MsysRoot "mingw64\bin"
$schemaSource = Join-Path $MsysRoot "mingw64\share\glib-2.0\schemas"
$stylePath = Join-Path $projectRoot "style.css"
$gdbusExe = Join-Path $mingwBin "gdbus.exe"

if (-not (Test-Path $exePath)) {
    throw "Built executable not found at $exePath"
}

if (-not (Test-Path $bashPath)) {
    throw "MSYS2 bash not found at $bashPath"
}

if (Test-Path $distPath) {
    $removed = Remove-DistDirectory -Path $distPath -ProjectRoot $projectRoot
    if (-not $removed) {
        if ($UseFallbackDistOnLock) {
            $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
            $fallbackName = "$DistDir-$timestamp"
            $distPath = Join-Path $projectRoot $fallbackName
            $binPath = Join-Path $distPath "bin"
            $libPath = Join-Path $distPath "lib"
            $configPath = Join-Path $distPath "config"
            $cachePath = Join-Path $distPath "cache"
            $logsPath = Join-Path $distPath "logs"
            $uiLogsPath = Join-Path $logsPath "ui"
            Write-Warning "Could not remove locked '$DistDir'. Packaging to '$fallbackName' instead."
        } else {
            throw "Could not remove locked dist directory '$distPath'. Close processes using it (for example liara-gtk-ui.exe or terminals with that as working directory) and retry."
        }
    }
}

New-Item -ItemType Directory -Path $distPath | Out-Null
New-Item -ItemType Directory -Path $binPath | Out-Null
New-Item -ItemType Directory -Path $libPath | Out-Null
New-Item -ItemType Directory -Path $cachePath | Out-Null
New-Item -ItemType Directory -Path $uiLogsPath -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $configPath "glib-2.0\schemas") -Force | Out-Null

Copy-Item $exePath -Destination (Join-Path $binPath "liara-gtk-ui.exe")

Get-ChildItem -Path $buildPath -Filter "*.dll" -File -ErrorAction SilentlyContinue |
    ForEach-Object {
        Copy-Item $_.FullName -Destination (Join-Path $libPath $_.Name)
    }

Copy-Item $stylePath -Destination (Join-Path $configPath "style.css")
if (Test-Path $gdbusExe) {
    Copy-Item $gdbusExe -Destination (Join-Path $binPath "gdbus.exe")
}

$projectRootUnix = ($projectRoot -replace '\\', '/') -replace '^([A-Za-z]):', '/$1'
$lddCommand = "export PATH=/mingw64/bin:/usr/bin:`$PATH; ldd $projectRootUnix/$BuildDir/liara-gtk-ui.exe"
$lddOutput = & $bashPath -lc $lddCommand

$dllPaths = $lddOutput |
    ForEach-Object {
        if ($_ -match '=> (/mingw64/bin/[^ ]+)') {
            $matches[1]
        }
    } |
    Sort-Object -Unique

foreach ($dllPath in $dllPaths) {
    $windowsPath = $dllPath -replace '^/mingw64/bin/', ($mingwBin.Replace('\', '/') + '/')
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
set "LOGFILE=%APPDIR%logs\ui\liara-gtk-ui.log"
set "XDG_CACHE_HOME=%APPDIR%cache"
set "XDG_DATA_DIRS=%APPDIR%config"
set "GSETTINGS_SCHEMA_DIR=%APPDIR%config\glib-2.0\schemas"
echo ==== [%date% %time%] launch ====>> "%LOGFILE%"
start "" cmd /c ""%APPDIR%bin\liara-gtk-ui.exe" >> "%LOGFILE%" 2>&1"
endlocal
'@

Set-Content -Path (Join-Path $distPath "run-liara-gtk-ui.cmd") -Value $launcher -Encoding ASCII

$devLauncher = @'
@echo off
setlocal
set "APPDIR=%~dp0"
set "PATH=%APPDIR%bin;%APPDIR%lib;%PATH%"
set "GTK_USE_PORTAL=0"
if not exist "%APPDIR%cache" mkdir "%APPDIR%cache"
if not exist "%APPDIR%logs\ui" mkdir "%APPDIR%logs\ui"
set "LOGFILE=%APPDIR%logs\ui\liara-gtk-ui.log"
set "XDG_CACHE_HOME=%APPDIR%cache"
set "XDG_DATA_DIRS=%APPDIR%config"
set "GSETTINGS_SCHEMA_DIR=%APPDIR%config\glib-2.0\schemas"
if "%LIARA_DEV_PASSWORD%"=="" (
  set "LIARA_DEV_PASSWORD=wmtool-liara-dev"
)
set "LIARA_DEV_MODE=1"
echo ==== [%date% %time%] launch-dev ====>> "%LOGFILE%"
start "" cmd /c ""%APPDIR%bin\liara-gtk-ui.exe" >> "%LOGFILE%" 2>&1"
endlocal
'@

Set-Content -Path (Join-Path $distPath "run-liara-gtk-ui-dev.cmd") -Value $devLauncher -Encoding ASCII

Write-Host "Packaged GTK UI to $distPath"
