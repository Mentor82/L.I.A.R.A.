param(
    [string]$BaseUrl = "http://127.0.0.1:8010",
    [string]$PythonExe = "c:/ai/LIARA/.venv/Scripts/python.exe",
    [string]$UserId = "wm",
    [int]$MaxTokens = 512,
    [string]$SessionId = "",
    [switch]$SkipDemo,
    [switch]$SkipPytest
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message)
}

if ([string]::IsNullOrWhiteSpace($SessionId)) {
    $SessionId = "demo-memory-" + [guid]::NewGuid().ToString("N").Substring(0, 8)
}

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

Write-Step "Repo root: $repoRoot"
Write-Step "Base URL: $BaseUrl"
Write-Step "Session ID: $SessionId"

if (-not $SkipDemo) {
    Write-Step "Running live chat demo script"
    & powershell -ExecutionPolicy Bypass -File ".\scripts\live_chat_memory_demo.ps1" `
        -BaseUrl $BaseUrl `
        -SessionId $SessionId `
        -UserId $UserId `
        -MaxTokens $MaxTokens
    if ($LASTEXITCODE -ne 0) {
        throw "live_chat_memory_demo.ps1 failed with exit code $LASTEXITCODE"
    }
}

if (-not $SkipPytest) {
    Write-Step "Running live pytest stream memory check"
    $env:RUN_LIVE_CHAT_STREAM_MEMORY_TESTS = "1"
    $env:LIARA_API_BASE_URL = $BaseUrl
    & $PythonExe -m pytest tests/integration/test_chat_stream_memory_effect_live.py -q
    if ($LASTEXITCODE -ne 0) {
        throw "pytest live chat memory check failed with exit code $LASTEXITCODE"
    }
}

Write-Step "All live chat memory checks passed"
Write-Host ""
Write-Host "Session ID: $SessionId" -ForegroundColor Cyan
