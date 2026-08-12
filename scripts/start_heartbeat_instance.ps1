param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8050
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "LIARA virtual environment not found: $python"
}

Set-Location -LiteralPath $root
& $python -m uvicorn services.heartbeat.app:app --host $HostAddress --port $Port --log-level info
