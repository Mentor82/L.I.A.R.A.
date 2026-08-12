param(
    [string]$BaseUrl = "http://127.0.0.1:8010",
    [string]$UserId = "wm",
    [int]$MaxTokens = 1200,
    [string]$SessionPrefix = "demo-scheduler-batch",
    [switch]$StopOnFirstFailure
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message)
}

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$prompts = @(
@"
🧠 Beispielprojekt: Modularer Task-Scheduler mit Plugin-System
Ein Python-Programm, das:
- Aufgaben (Tasks) verwaltet
- unterschiedliche Task-Typen über Plugins lädt
- einen Scheduler-Kern hat
- ein Event-System besitzt
- Logs, State und Config getrennt hält
- und sauber modular aufgebaut ist
Nicht zu leicht, nicht zu komplex.
"@,
@"
Entwirf ein mittleres Python-Projekt: modularer Task-Scheduler mit Plugin-Architektur.
Erwarte:
1) Dateibaum,
2) Verantwortlichkeiten je Modul,
3) Scheduler-Flow mit Events,
4) Trennung von Logging, State, Config,
5) kleines Kern-Code-Skelett.
"@,
@"
Bitte erstelle eine strukturierte Projektvorlage in Python für einen modularen Task-Scheduler.
Muss enthalten: Task-Registry, Plugin-Loader für verschiedene Task-Typen, Scheduler-Kern,
Event-Bus, separate Schichten für Config/State/Logging und eine pragmatische modulare Struktur.
"@
)

Write-Step "Repo root: $repoRoot"
Write-Step "Base URL: $BaseUrl"
Write-Step "Prompt variants: $($prompts.Count)"

$results = New-Object System.Collections.Generic.List[object]

for ($i = 0; $i -lt $prompts.Count; $i++) {
    $index = $i + 1
    $sessionId = "$SessionPrefix-$index-" + [guid]::NewGuid().ToString("N").Substring(0, 6)

    Write-Step "Running variant $index with session $sessionId"

    $output = & powershell -ExecutionPolicy Bypass -File ".\scripts\live_chat_scheduler_demo.ps1" `
        -BaseUrl $BaseUrl `
        -SessionId $sessionId `
        -UserId $UserId `
        -MaxTokens $MaxTokens `
        -TaskPrompt $prompts[$i] 2>&1

    $exitCode = $LASTEXITCODE
    $rawText = ($output | Out-String)

    $logPath = ""
    $logMatch = [regex]::Match($rawText, "Demo log:\s*(.+)")
    if ($logMatch.Success) {
        $logPath = $logMatch.Groups[1].Value.Trim()
    }

    $result = [pscustomobject]@{
        Variant   = $index
        SessionId = $sessionId
        ExitCode  = $exitCode
        Passed    = ($exitCode -eq 0)
        LogPath   = $logPath
    }
    $results.Add($result) | Out-Null

    if ($result.Passed) {
        Write-Step "Variant $index passed"
    } else {
        Write-Step "Variant $index failed (exit=$exitCode)"
        if (-not [string]::IsNullOrWhiteSpace($logPath)) {
            Write-Step "See log: $logPath"
        }
        if ($StopOnFirstFailure) {
            break
        }
    }
}

$passed = @($results | Where-Object { $_.Passed }).Count
$total = $results.Count
$rate = if ($total -gt 0) { [math]::Round(($passed / $total) * 100, 1) } else { 0 }

Write-Host ""
Write-Host "=== Live Scheduler Check Summary ==="
$results | Format-Table Variant, SessionId, ExitCode, Passed, LogPath -AutoSize
Write-Host ""
Write-Host ("Pass rate: {0}/{1} ({2}%)" -f $passed, $total, $rate)

if ($passed -eq $total -and $total -gt 0) {
    exit 0
}

exit 2
