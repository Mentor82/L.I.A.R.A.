param(
    [string]$Token,
    [string]$AuthorizationId = ("operator-canary-" + [DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssZ")),
    [string]$Reason = "Calibrate the real LIARA assurance feedback path",
    [string]$ObserverBaseUrl = "http://127.0.0.1:8060",
    [string]$ApiBaseUrl = "http://127.0.0.1:8010",
    [string]$MemoryBaseUrl = "http://127.0.0.1:8020",
    [int]$TimeoutSeconds = 900,
    [switch]$AllowStaleAssuranceRecovery,
    [switch]$AllowFailedAssuranceRetry,
    [switch]$ResumeExisting,
    [switch]$VerifyPersistenceOnly
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$headers = @{ Authorization = "Bearer $Token" }

function Get-Inspection {
    Invoke-RestMethod -Uri "$ObserverBaseUrl/v1/inspection" -TimeoutSec 15
}

if ($VerifyPersistenceOnly) {
    $persisted = Get-Inspection
    $checks = [ordered]@{
        authorization_id_persisted = $persisted.authorization_id -eq $AuthorizationId
        job_id_persisted = -not [string]::IsNullOrWhiteSpace([string]$persisted.job_id)
        minimum_interval_persisted = $null -ne $persisted.last_submitted_at -and $null -ne $persisted.next_eligible_at
        mode_locked_to_observe = $persisted.mode -eq "observe"
        terminal_state_persisted = $persisted.job_state -in @("completed", "failed")
    }
    $persistenceResult = [pscustomobject]@{
        authorization_id = $AuthorizationId
        job_id = $persisted.job_id
        checks = $checks
    }
    $reportPath = Join-Path $projectRoot "artifacts\self_inspection_canary\$AuthorizationId.json"
    if (Test-Path -LiteralPath $reportPath) {
        $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
        $report | Add-Member -NotePropertyName persistence_after_restart -NotePropertyValue $checks -Force
        $report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $reportPath -Encoding utf8
    }
    $persistenceResult | ConvertTo-Json -Depth 8
    if ($checks.Values -contains $false) { exit 1 }
    exit 0
}

$state = Invoke-RestMethod -Uri "$ObserverBaseUrl/v1/state" -TimeoutSec 15
if ($state.state -ne "healthy") {
    $hardware = @($state.evidence | Where-Object { $_.domain -eq "hardware" }) | Select-Object -First 1
    $software = @($state.evidence | Where-Object { $_.domain -eq "software" }) | Select-Object -First 1
    $assurance = @($state.evidence | Where-Object { $_.domain -eq "assurance" }) | Select-Object -First 1
    $allowedRecoverySignals = @("validator_evidence_stale", "validator_findings", "validator_job_failed")
    $unexpectedSignals = @($state.signals | Where-Object { $_ -notin $allowedRecoverySignals })
    $staleAssuranceRecovery = $AllowStaleAssuranceRecovery `
        -and $hardware.state -eq "healthy" `
        -and $software.state -eq "healthy" `
        -and $assurance.signals -contains "validator_evidence_stale" `
        -and $unexpectedSignals.Count -eq 0
    $failedAssuranceRetry = $AllowFailedAssuranceRetry `
        -and $hardware.state -eq "healthy" `
        -and $software.state -eq "healthy" `
        -and $assurance.attributes.job_state -eq "failed" `
        -and $unexpectedSignals.Count -eq 0
    if (-not $ResumeExisting -and -not $staleAssuranceRecovery -and -not $failedAssuranceRetry) {
        throw "Canary blocked: observed system state is '$($state.state)', expected healthy"
    }
}

if ($ResumeExisting) {
    $decision = Get-Inspection
    if ($decision.authorization_id -ne $AuthorizationId -or [string]::IsNullOrWhiteSpace([string]$decision.job_id)) {
        throw "No matching existing canary for '$AuthorizationId'"
    }
} else {
    if ([string]::IsNullOrWhiteSpace($Token)) { throw "Token is required for a new canary" }
    $payload = @{
        authorization_id = $AuthorizationId
        reason = $Reason
    } | ConvertTo-Json
    $decision = Invoke-RestMethod -Method Post -Uri "$ObserverBaseUrl/v1/inspection/canary" `
        -Headers $headers -ContentType "application/json" -Body $payload -TimeoutSec 45
    if ($decision.action -ne "submitted" -or [string]::IsNullOrWhiteSpace([string]$decision.job_id)) {
        throw "Canary was not submitted: $($decision | ConvertTo-Json -Depth 8 -Compress)"
    }
}

$jobId = [string]$decision.job_id
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
do {
    Start-Sleep -Seconds 1
    $decision = Get-Inspection
    if ($decision.job_id -ne $jobId) {
        throw "Gate job id changed from '$jobId' to '$($decision.job_id)'"
    }
} while ($decision.job_state -in @("queued", "running") -and (Get-Date) -lt $deadline)

if ($decision.job_state -in @("queued", "running")) {
    throw "Canary timed out in state '$($decision.job_state)'"
}

$validatorResult = Invoke-RestMethod -Method Post -Uri "$MemoryBaseUrl/validator/result" `
    -ContentType "application/json" -Body (@{ job_id = $jobId } | ConvertTo-Json) -TimeoutSec 30

$observerDeadline = (Get-Date).AddSeconds(60)
do {
    $operations = Invoke-RestMethod -Uri "$ApiBaseUrl/operations/self-observer?history_limit=8" -TimeoutSec 20
    $assurance = $operations.state.evidence | Where-Object { $_.domain -eq "assurance" }
    if ($assurance.attributes.job_id -eq $jobId) { break }
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $observerDeadline)

$auditPath = Join-Path $projectRoot "logs\services\sys_audit.jsonl"
$auditEntries = @()
if (Test-Path -LiteralPath $auditPath) {
    $auditEntries = Get-Content -LiteralPath $auditPath | ForEach-Object {
        try { $_ | ConvertFrom-Json } catch { $null }
    } | Where-Object {
        $_ -and $_.request_id -eq $decision.request_id -and (($_.args -join " ") -match [regex]::Escape($jobId))
    }
}
$submitAudit = @($auditEntries | Where-Object { ($_.args -join " ") -match "validator_submit" })
$executeAudit = @($auditEntries | Where-Object { ($_.args -join " ") -match "validator_execute" })
$transitionStates = @($decision.transitions | ForEach-Object { $_.state })
$validatorCommand = @($validatorResult.summary.command)
$validatorScope = if ($validatorResult.summary.scope) {
    [string]$validatorResult.summary.scope
} elseif ($validatorCommand.Count -gt 0) {
    [string]$validatorCommand[-1]
} else {
    ""
}

$checks = [ordered]@{
    exactly_one_job_submitted = $submitAudit.Count -eq 1
    scope_is_quick = $decision.scope -eq "quick" -and $validatorScope -eq "quick"
    minimum_interval_persisted = $null -ne $decision.last_submitted_at -and $null -ne $decision.next_eligible_at
    transitions_complete = (@("queued", "running", $decision.job_state) | ForEach-Object { $transitionStates -contains $_ }) -notcontains $false
    findings_exit_code_separate = ($decision.PSObject.Properties.Name -contains "exit_code") -and $null -ne $decision.findings
    job_id_in_audit = $submitAudit.Count -eq 1 -and $executeAudit.Count -eq 1
    job_id_in_observer = $assurance.attributes.job_id -eq $jobId
    job_id_in_validator = $validatorResult.job_id -eq $jobId
    gate_is_observe = $decision.mode -eq "observe"
}

$report = [ordered]@{
    schema_version = "1.0"
    authorization_id = $AuthorizationId
    observed_state = @{ state = $state.state; phase = $state.phase; sequence = $state.sequence }
    stale_assurance_recovery = [bool]$AllowStaleAssuranceRecovery
    failed_assurance_retry = [bool]$AllowFailedAssuranceRetry
    job_id = $jobId
    request_id = $decision.request_id
    run_id = $decision.run_id
    scope = $decision.scope
    job_state = $decision.job_state
    transitions = $decision.transitions
    exit_code = $decision.exit_code
    findings = $decision.findings
    artifacts = $decision.artifacts
    checks = $checks
}

$reportDir = Join-Path $projectRoot "artifacts\self_inspection_canary"
New-Item -ItemType Directory -Path $reportDir -Force | Out-Null
$reportPath = Join-Path $reportDir "$AuthorizationId.json"
$report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $reportPath -Encoding utf8
$report | ConvertTo-Json -Depth 12
Write-Host "Report: $reportPath"
if ($checks.Values -contains $false) { exit 1 }
