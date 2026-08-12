param(
    [string]$BaseUrl = "http://127.0.0.1:8010",
    [string]$SessionId = ("demo-scheduler-" + [guid]::NewGuid().ToString("N").Substring(0, 8)),
    [string]$UserId = "wm",
    [int]$MaxTokens = 1200,
    [string]$TaskPrompt = @"
Beispielprojekt: Modularer Task-Scheduler mit Plugin-System.
Erstelle einen klaren Projektentwurf in Python mit folgenden Punkten:
- Aufgaben (Tasks) verwalten
- unterschiedliche Task-Typen ueber Plugins laden
- Scheduler-Kern
- Event-System
- Logs, State und Config getrennt halten
- sauber modular aufgebaut
Nicht zu leicht, nicht zu komplex.
Bitte gib:
1) Modulstruktur (Dateibaum),
2) kurze Responsibilities je Modul,
3) zentralen Ablauf (Task -> Scheduler -> Event -> Logging/State),
4) kleines lauffaehiges Code-Skelett (nur Kern, kein Overengineering).
"@,
    [string]$LogPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Net.Http

if ([string]::IsNullOrWhiteSpace($LogPath)) {
    $logDir = Join-Path $PSScriptRoot "..\logs\demos"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $LogPath = Join-Path $logDir "live-chat-scheduler-demo-$timestamp.log"
} else {
    $parent = Split-Path -Parent $LogPath
    if ($parent) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
}

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

function Write-Log {
    param(
        [string]$Message,
        [string]$Level = "INFO"
    )

    $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss.fff")
    $line = "[$stamp] [$Level] $Message"
    Write-Host $line
    [System.IO.File]::AppendAllText($LogPath, $line + [Environment]::NewLine, $utf8NoBom)
}

function Get-JsonProp {
    param(
        [Parameter(Mandatory = $true)]
        $Object,
        [Parameter(Mandatory = $true)]
        [string]$Name,
        $Default = $null
    )

    if ($null -eq $Object) {
        return $Default
    }

    $prop = $Object.PSObject.Properties[$Name]
    if ($null -ne $prop) {
        return $prop.Value
    }
    return $Default
}

function Test-ResponseCoverage {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Text
    )

    $lower = $Text.ToLowerInvariant()
    $checks = @("plugin", "scheduler", "event", "config", "state", "log", "task", "modul")
    $hits = @()
    foreach ($kw in $checks) {
        if ($lower.Contains($kw)) {
            $hits += $kw
        }
    }

    Write-Log ("coverage hits={0} matched={1}" -f $hits.Count, ($hits -join ","))
    return ($hits.Count -ge 6)
}

function Invoke-LiveStreamTurn {
    param([string]$Message)

    $payload = @{
        session_id = $SessionId
        user_id    = $UserId
        message    = $Message
        max_tokens = $MaxTokens
    } | ConvertTo-Json -Depth 5

    $handler = [System.Net.Http.HttpClientHandler]::new()
    $client = [System.Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(240)

    $request = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Post, "$BaseUrl/chat/stream")
    $request.Content = [System.Net.Http.StringContent]::new($payload, $utf8NoBom, "application/json")
    $request.Headers.Accept.ParseAdd("text/event-stream")

    $currentEvent = ""
    $chunkBuilder = [System.Text.StringBuilder]::new()

    try {
        $response = $client.SendAsync($request, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
        [void]$response.EnsureSuccessStatusCode()

        $stream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        $reader = [System.IO.StreamReader]::new($stream, $utf8NoBom)

        while (-not $reader.EndOfStream) {
            $line = $reader.ReadLine()
            if ($null -eq $line -or [string]::IsNullOrWhiteSpace($line)) {
                continue
            }

            if ($line.StartsWith("event:")) {
                $currentEvent = $line.Substring(6).Trim()
                continue
            }
            if (-not $line.StartsWith("data:")) {
                continue
            }

            $rawData = $line.Substring(5).Trim()
            switch ($currentEvent) {
                "progress" {
                    $obj = $rawData | ConvertFrom-Json
                    $stage = [string](Get-JsonProp -Object $obj -Name "stage" -Default "progress")
                    $msg = [string](Get-JsonProp -Object $obj -Name "message" -Default "")
                    Write-Log "progress: $stage -> $msg"
                }
                "heartbeat" {
                    $obj = $rawData | ConvertFrom-Json
                    $stage = [string](Get-JsonProp -Object $obj -Name "stage" -Default "running")
                    $elapsedMs = [string](Get-JsonProp -Object $obj -Name "elapsed_ms" -Default "")
                    Write-Log "heartbeat: stage=$stage elapsed_ms=$elapsedMs"
                }
                "chunk" {
                    $obj = $rawData | ConvertFrom-Json
                    $text = [string](Get-JsonProp -Object $obj -Name "text" -Default "")
                    [void]$chunkBuilder.Append($text)
                }
                "final" {
                    $finalPayload = $rawData | ConvertFrom-Json
                    $meta = Get-JsonProp -Object $finalPayload -Name "metadata" -Default $null
                    $ctx = Get-JsonProp -Object $meta -Name "context_debug" -Default $null
                    $ctxMode = [string](Get-JsonProp -Object $ctx -Name "mode" -Default "UNKNOWN")
                    $provider = [string](Get-JsonProp -Object $finalPayload -Name "llm_provider" -Default "unknown")
                    $model = [string](Get-JsonProp -Object $finalPayload -Name "llm_model" -Default "unknown")
                    Write-Log "final: mode=$ctxMode provider=$provider model=$model"
                }
                "done" {
                    Write-Log "done"
                    break
                }
                default {
                    Write-Log "event '$currentEvent'"
                }
            }
        }
    } finally {
        if ($null -ne $client) {
            [void]$client.Dispose()
        }
    }

    return $chunkBuilder.ToString()
}

Write-Log "Live chat scheduler demo started"
Write-Log "BaseUrl=$BaseUrl SessionId=$SessionId UserId=$UserId"
Write-Log "Log file: $LogPath"

try {
    $responseText = Invoke-LiveStreamTurn -Message $TaskPrompt
    Write-Log ("response length: {0} chars" -f $responseText.Length)
    if ($responseText.Length -gt 0) {
        $preview = $responseText.Substring(0, [Math]::Min(280, $responseText.Length)).Replace("`r", " ").Replace("`n", " ")
        Write-Log "response preview: $preview"
    }

    if (Test-ResponseCoverage -Text $responseText) {
        Write-Log "Scheduler architecture response includes required concepts." "SUCCESS"
        Write-Host ""
        Write-Host "Demo log: $LogPath" -ForegroundColor Cyan
        exit 0
    }

    Write-Log "Response did not include enough required scheduler concepts." "ERROR"
    Write-Host ""
    Write-Host "Demo log: $LogPath" -ForegroundColor Yellow
    exit 2
} catch {
    Write-Log "Demo failed: $($_.Exception.Message)" "ERROR"
    Write-Host ""
    Write-Host "Demo log: $LogPath" -ForegroundColor Red
    throw
}
