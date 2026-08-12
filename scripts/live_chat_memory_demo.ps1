param(
    [string]$BaseUrl = "http://127.0.0.1:8010",
    [string]$SessionId = ("demo-memory-" + [guid]::NewGuid().ToString("N").Substring(0, 8)),
    [string]$UserId = "wm",
    [string]$FirstMessage = "Mein Name ist Mira.",
    [string]$SecondMessage = "Wie heisse ich?",
    [int]$MaxTokens = 512,
    [string]$LogPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Net.Http

if ([string]::IsNullOrWhiteSpace($LogPath)) {
    $logDir = Join-Path $PSScriptRoot "..\logs\demos"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $LogPath = Join-Path $logDir "live-chat-memory-demo-$timestamp.log"
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

function Invoke-LiveStreamTurn {
    param(
        [string]$Message,
        [int]$Turn
    )

    $payload = @{
        session_id = $SessionId
        user_id    = $UserId
        message    = $Message
        max_tokens = $MaxTokens
    } | ConvertTo-Json -Depth 5

    $handler = [System.Net.Http.HttpClientHandler]::new()
    $client = [System.Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(180)

    $request = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Post, "$BaseUrl/chat/stream")
    $request.Content = [System.Net.Http.StringContent]::new($payload, $utf8NoBom, "application/json")
    $request.Headers.Accept.ParseAdd("text/event-stream")

    $currentEvent = ""
    $chunkBuilder = [System.Text.StringBuilder]::new()
    $progressStages = New-Object System.Collections.Generic.List[string]
    $memoryEffectDetected = $false
    $finalPayload = $null

    Write-Log "TURN $Turn request: $Message"

    try {
        $responseTask = $client.SendAsync($request, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead)
        $response = $responseTask.GetAwaiter().GetResult()
        [void]$response.EnsureSuccessStatusCode()

        $streamTask = $response.Content.ReadAsStreamAsync()
        $stream = $streamTask.GetAwaiter().GetResult()
        $reader = [System.IO.StreamReader]::new($stream, $utf8NoBom)

        while (-not $reader.EndOfStream) {
            $line = $reader.ReadLine()
            if ($null -eq $line) {
                continue
            }
            if ([string]::IsNullOrWhiteSpace($line)) {
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
                    $meta = Get-JsonProp -Object $obj -Name "metadata" -Default $null
                    $progressStages.Add($stage) | Out-Null
                    if ($stage -eq "memory_effect_detected") {
                        $memoryEffectDetected = $true
                    }
                    $modeSuffix = ""
                    $contextMode = Get-JsonProp -Object $meta -Name "context_mode" -Default $null
                    if ($null -ne $contextMode) {
                        $modeSuffix = " | mode=$contextMode"
                    }
                    Write-Log "TURN $Turn progress: $stage -> $msg$modeSuffix"
                }
                "heartbeat" {
                    $obj = $rawData | ConvertFrom-Json
                    $stage = [string](Get-JsonProp -Object $obj -Name "stage" -Default "running")
                    $elapsedMs = [string](Get-JsonProp -Object $obj -Name "elapsed_ms" -Default "")
                    Write-Log "TURN $Turn heartbeat: stage=$stage elapsed_ms=$elapsedMs"
                }
                "chunk" {
                    $obj = $rawData | ConvertFrom-Json
                    $text = [string]$obj.text
                    [void]$chunkBuilder.Append($text)
                    Write-Log "TURN $Turn chunk[$($obj.index)]: $text"
                }
                "final" {
                    $finalPayload = $rawData | ConvertFrom-Json
                    $meta = Get-JsonProp -Object $finalPayload -Name "metadata" -Default $null
                    $ctx = Get-JsonProp -Object $meta -Name "context_debug" -Default $null
                    $ctxMode = [string](Get-JsonProp -Object $ctx -Name "mode" -Default "UNKNOWN")
                    $provider = [string](Get-JsonProp -Object $finalPayload -Name "llm_provider" -Default "unknown")
                    $model = [string](Get-JsonProp -Object $finalPayload -Name "llm_model" -Default "unknown")
                    Write-Log "TURN $Turn final: mode=$ctxMode provider=$provider model=$model"
                }
                "done" {
                    Write-Log "TURN $Turn done"
                    break
                }
                default {
                    Write-Log "TURN $Turn event '$currentEvent': $rawData"
                }
            }
        }
    } finally {
        if ($null -ne $client) {
            [void]$client.Dispose()
        }
    }

    $responseText = $chunkBuilder.ToString()
    return [pscustomobject]@{
        message                = $Message
        response_text          = $responseText
        final_payload          = $finalPayload
        progress_stages        = @($progressStages)
        memory_effect_detected = $memoryEffectDetected
    }
}

Write-Log "Live chat memory demo started"
Write-Log "BaseUrl=$BaseUrl SessionId=$SessionId UserId=$UserId"
Write-Log "Log file: $LogPath"

try {
    $first = Invoke-LiveStreamTurn -Message $FirstMessage -Turn 1
    Write-Log "TURN 1 response_text: $($first.response_text)"

    $second = Invoke-LiveStreamTurn -Message $SecondMessage -Turn 2
    Write-Log "TURN 2 response_text: $($second.response_text)"

    $summary = @(
        "SessionId: $SessionId"
        "Turn1 progress: $([string]::Join(', ', $first.progress_stages))"
        "Turn2 progress: $([string]::Join(', ', $second.progress_stages))"
        "Turn2 memory effect detected: $($second.memory_effect_detected)"
    )
    Write-Log "SUMMARY:`n$($summary -join [Environment]::NewLine)"

    if ($second.memory_effect_detected -or $second.response_text -match "Mira") {
        Write-Log "Memory effect observed in second turn." "SUCCESS"
        Write-Host ""
        Write-Host "Demo log: $LogPath" -ForegroundColor Cyan
        exit 0
    }

    Write-Log "No memory effect detected in second turn." "ERROR"
    Write-Host ""
    Write-Host "Demo log: $LogPath" -ForegroundColor Yellow
    exit 2
} catch {
    Write-Log "Demo failed: $($_.Exception.Message)" "ERROR"
    Write-Host ""
    Write-Host "Demo log: $LogPath" -ForegroundColor Red
    throw
}
