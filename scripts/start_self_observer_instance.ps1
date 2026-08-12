param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8060
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "LIARA Python runtime not found: $python"
}

Set-Location -LiteralPath $projectRoot
& $python -m uvicorn services.self_observer.app:app --host $HostAddress --port $Port
