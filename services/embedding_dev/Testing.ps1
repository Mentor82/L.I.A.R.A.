Set-StrictMode -Version Latest

$ServiceUrl = "http://127.0.0.1:8033"
$EndpointGenerate = "$ServiceUrl/embedding/generate"
$EndpointHealth   = "$ServiceUrl/health"

$script:PassCount = 0
$script:FailCount = 0
$script:WarnCount = 0
$script:UtilRecorder = $null

function Write-Section($Title) {
    Write-Host ""
    Write-Host "--- $Title ---" -ForegroundColor Cyan
}

function Write-Result($Status, $Message) {
    switch ($Status) {
        "PASS" {
            $script:PassCount++
            Write-Host "[PASS] $Message" -ForegroundColor Green
        }
        "FAIL" {
            $script:FailCount++
            Write-Host "[FAIL] $Message" -ForegroundColor Red
        }
        "WARN" {
            $script:WarnCount++
            Write-Host "[WARN] $Message" -ForegroundColor Yellow
        }
        default {
            Write-Host "[$Status] $Message"
        }
    }
}

function Get-Embedding($Text, [bool]$Normalize = $true) {
    $body = @{
        input_text = $Text
        normalize  = $Normalize
    } | ConvertTo-Json

    Invoke-RestMethod `
        -Uri $EndpointGenerate `
        -Method POST `
        -Body $body `
        -ContentType "application/json"
}

function Get-CosineSimilarity($A, $B) {
    if ($null -eq $A -or $null -eq $B) {
        return 0.0
    }
    if ($A.Count -ne $B.Count) {
        throw "Vector length mismatch: A=$($A.Count), B=$($B.Count)"
    }

    $dot = 0.0
    $normA = 0.0
    $normB = 0.0

    for ($i = 0; $i -lt $A.Count; $i++) {
        $dot += $A[$i] * $B[$i]
        $normA += $A[$i] * $A[$i]
        $normB += $B[$i] * $B[$i]
    }

    if ($normA -le 0 -or $normB -le 0) {
        return 0.0
    }

    $dot / ([Math]::Sqrt($normA) * [Math]::Sqrt($normB))
}

function Get-L2Norm($Vector) {
    if ($null -eq $Vector -or $Vector.Count -eq 0) {
        return 0.0
    }

    [Math]::Sqrt((($Vector | ForEach-Object { $_ * $_ } | Measure-Object -Sum).Sum))
}

function Assert-Similar($Label, $SimValue, $Threshold = 0.85) {
    $rounded = [Math]::Round($SimValue, 4)
    if ($SimValue -ge $Threshold) {
        Write-Result "PASS" "$Label  sim=$rounded  (>= $Threshold)"
    } else {
        Write-Result "FAIL" "$Label  sim=$rounded  (>= $Threshold)"
    }
}

function Assert-Dissimilar($Label, $SimValue, $Threshold = 0.75) {
    $rounded = [Math]::Round($SimValue, 4)
    if ($SimValue -lt $Threshold) {
        Write-Result "PASS" "$Label  sim=$rounded  (< $Threshold)"
    } else {
        Write-Result "FAIL" "$Label  sim=$rounded  (< $Threshold)"
    }
}

function Assert-True($Label, $Condition, $Detail = "") {
    if ($Condition) {
        Write-Result "PASS" "$Label $Detail".Trim()
    } else {
        Write-Result "FAIL" "$Label $Detail".Trim()
    }
}

function Assert-WarnIf($Label, $Condition, $Detail = "") {
    if ($Condition) {
        Write-Result "WARN" "$Label $Detail".Trim()
    } else {
        Write-Result "PASS" "$Label $Detail".Trim()
    }
}

function Get-StatSummary($Values) {
    if ($null -eq $Values -or $Values.Count -eq 0) {
        return $null
    }
    $min = ($Values | Measure-Object -Minimum).Minimum
    $avg = ($Values | Measure-Object -Average).Average
    $max = ($Values | Measure-Object -Maximum).Maximum
    [PSCustomObject]@{
        Min = [double]$min
        Avg = [double]$avg
        Max = [double]$max
    }
}

function New-AsciiSparkline($Values, [int]$Width = 60) {
    if ($null -eq $Values -or $Values.Count -eq 0) {
        return "n/a"
    }

    $chars = " .:-=+*#%@"
    $vals = @($Values | ForEach-Object { [double]$_ })
    $bucketCount = [Math]::Min($Width, $vals.Count)
    if ($bucketCount -lt 1) {
        return "n/a"
    }

    $bucketSize = $vals.Count / [double]$bucketCount
    $buckets = @()
    for ($i = 0; $i -lt $bucketCount; $i++) {
        $start = [int][Math]::Floor($i * $bucketSize)
        $end = [int][Math]::Floor(($i + 1) * $bucketSize) - 1
        if ($end -lt $start) { $end = $start }
        if ($end -ge $vals.Count) { $end = $vals.Count - 1 }

        $slice = @()
        for ($j = $start; $j -le $end; $j++) {
            $slice += $vals[$j]
        }
        $buckets += ($slice | Measure-Object -Average).Average
    }

    $line = ""
    foreach ($v in $buckets) {
        $clamped = [Math]::Max(0.0, [Math]::Min(100.0, [double]$v))
        $idx = [int][Math]::Round(($clamped / 100.0) * ($chars.Length - 1))
        $line += $chars[$idx]
    }
    return $line
}

function Start-UtilizationRecorder([int]$IntervalMs = 1000) {
    $samplePath = [System.IO.Path]::GetTempFileName()

    $job = Start-Job -ScriptBlock {
        param($OutPath, $SleepMs)

        $ErrorActionPreference = "SilentlyContinue"

        function Resolve-NpuCounterPath {
            $setNamePatterns = @(
                "NPU",
                "Neural",
                "AI",
                "Intel.*Boost",
                "Beschleuniger",
                "Neuron"
            )
            $metricPatterns = @(
                "Utilization",
                "Usage",
                "Busy",
                "Compute",
                "Auslastung",
                "Nutzung"
            )

            $tryPaths = New-Object System.Collections.Generic.List[string]

            try {
                $sets = Get-Counter -ListSet *
                foreach ($set in $sets) {
                    $setName = [string]$set.CounterSetName
                    if (-not ($setNamePatterns | Where-Object { $setName -match $_ })) {
                        continue
                    }
                    foreach ($p in $set.Paths) {
                        $pathStr = [string]$p
                        if ($metricPatterns | Where-Object { $pathStr -match $_ }) {
                            if (-not $tryPaths.Contains($pathStr)) {
                                [void]$tryPaths.Add($pathStr)
                            }
                        }
                    }
                }
            }
            catch {}

            try {
                $raw = typeperf -qx 2>$null
                foreach ($line in $raw) {
                    if ([string]::IsNullOrWhiteSpace($line)) {
                        continue
                    }
                    $txt = [string]$line
                    $hasDeviceHint = $txt -match "NPU|Neural|AI|Intel\(R\) AI Boost|Beschleuniger|Neuron"
                    $hasMetricHint = $txt -match "Utilization|Usage|Busy|Compute|Auslastung|Nutzung"
                    if ($hasDeviceHint -and $hasMetricHint) {
                        if (-not $tryPaths.Contains($txt)) {
                            [void]$tryPaths.Add($txt)
                        }
                    }
                }
            }
            catch {}

            foreach ($candidate in $tryPaths) {
                try {
                    $probe = Get-Counter $candidate -ErrorAction Stop
                    if ($null -ne $probe -and $probe.CounterSamples.Count -gt 0) {
                        return [PSCustomObject]@{
                            Path = $candidate
                            Source = "counter"
                        }
                    }
                }
                catch {}
            }

            return [PSCustomObject]@{
                Path = $null
                Source = "none"
            }
        }

        $npuCounter = Resolve-NpuCounterPath
        $npuCounterPath = $npuCounter.Path
        $npuSource = $npuCounter.Source

        while ($true) {
            $ts = (Get-Date).ToString("o")

            $cpu = 0.0
            $cpuS = Get-Counter "\Processor(_Total)\% Processor Time"
            if ($null -ne $cpuS -and $cpuS.CounterSamples.Count -gt 0) {
                $cpu = [double]$cpuS.CounterSamples[0].CookedValue
            }

            $gpu = 0.0
            $gpuS = Get-Counter "\GPU Engine(*)\Utilization Percentage"
            if ($null -ne $gpuS -and $gpuS.CounterSamples.Count -gt 0) {
                $active = $gpuS.CounterSamples | Where-Object { $_.CookedValue -gt 0 }
                if ($active.Count -gt 0) {
                    $gpu = ($active | Measure-Object -Property CookedValue -Sum).Sum
                }
            }
            if ($gpu -gt 100.0) { $gpu = 100.0 }

            $npu = $null
            if ($null -ne $npuCounterPath) {
                $npuS = Get-Counter $npuCounterPath
                if ($null -ne $npuS -and $npuS.CounterSamples.Count -gt 0) {
                    $sampleVals = @($npuS.CounterSamples | ForEach-Object { [double]$_.CookedValue })
                    if ($sampleVals.Count -gt 0) {
                        $npu = ($sampleVals | Measure-Object -Average).Average
                    }
                }
            }

            [PSCustomObject]@{
                ts            = $ts
                cpu           = [Math]::Max(0.0, [Math]::Min(100.0, $cpu))
                gpu           = [Math]::Max(0.0, [Math]::Min(100.0, $gpu))
                npu           = $npu
                npu_source    = $npuSource
                npu_counter   = $npuCounterPath
            } | ConvertTo-Json -Compress | Add-Content -Path $OutPath -Encoding UTF8

            Start-Sleep -Milliseconds $SleepMs
        }
    } -ArgumentList $samplePath, $IntervalMs

    [PSCustomObject]@{
        Job       = $job
        SampleLog = $samplePath
    }
}

function Stop-UtilizationRecorder($Recorder) {
    if ($null -eq $Recorder) {
        return $null
    }

    if ($null -ne $Recorder.Job) {
        Stop-Job -Job $Recorder.Job -ErrorAction SilentlyContinue | Out-Null
        Receive-Job -Job $Recorder.Job -ErrorAction SilentlyContinue | Out-Null
        Remove-Job -Job $Recorder.Job -Force -ErrorAction SilentlyContinue | Out-Null
    }

    if (-not (Test-Path $Recorder.SampleLog)) {
        return @()
    }

    $rows = Get-Content -Path $Recorder.SampleLog -ErrorAction SilentlyContinue
    Remove-Item -Path $Recorder.SampleLog -Force -ErrorAction SilentlyContinue

    $samples = @()
    foreach ($line in $rows) {
        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }
        $samples += ($line | ConvertFrom-Json)
    }

    return $samples
}

function Show-UtilizationReport($Samples) {
    Write-Section "Resource Utilization (CPU/GPU/NPU)"

    if ($null -eq $Samples -or $Samples.Count -eq 0) {
        Write-Result "WARN" "no utilization samples collected"
        return
    }

    $cpuValues = @($Samples | ForEach-Object { [double]$_.cpu })
    $gpuValues = @($Samples | ForEach-Object { [double]$_.gpu })
    $npuValues = @($Samples | Where-Object { $null -ne $_.npu } | ForEach-Object { [double]$_.npu })
    $npuCounterPath = ($Samples | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_.npu_counter) } | Select-Object -First 1 -ExpandProperty npu_counter)
    $npuSource = ($Samples | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_.npu_source) } | Select-Object -First 1 -ExpandProperty npu_source)

    $cpuStats = Get-StatSummary $cpuValues
    $gpuStats = Get-StatSummary $gpuValues
    $npuStats = Get-StatSummary $npuValues

    Write-Host "samples=$($Samples.Count)"

    if ($null -ne $cpuStats) {
        Write-Host ("CPU min={0} avg={1} max={2}" -f ([Math]::Round($cpuStats.Min,2)), ([Math]::Round($cpuStats.Avg,2)), ([Math]::Round($cpuStats.Max,2)))
        Write-Host ("CPU {0}" -f (New-AsciiSparkline $cpuValues 70))
    }

    if ($null -ne $gpuStats) {
        Write-Host ("GPU min={0} avg={1} max={2}" -f ([Math]::Round($gpuStats.Min,2)), ([Math]::Round($gpuStats.Avg,2)), ([Math]::Round($gpuStats.Max,2)))
        Write-Host ("GPU {0}" -f (New-AsciiSparkline $gpuValues 70))
    }

    if ($null -ne $npuStats) {
        Write-Host ("NPU min={0} avg={1} max={2}" -f ([Math]::Round($npuStats.Min,2)), ([Math]::Round($npuStats.Avg,2)), ([Math]::Round($npuStats.Max,2)))
        Write-Host ("NPU {0}" -f (New-AsciiSparkline $npuValues 70))
        if (-not [string]::IsNullOrWhiteSpace([string]$npuCounterPath)) {
            Write-Host ("NPU counter path: {0}" -f $npuCounterPath)
        }
    }
    else {
        if ([string]::IsNullOrWhiteSpace([string]$npuSource)) {
            $npuSource = "none"
        }
        Write-Result "WARN" "NPU counters not available on this host (source=$npuSource)"
    }
}

Write-Host ""
Write-Host "=== Embedding Service Aggressive Tests ===" -ForegroundColor Magenta
Write-Host "(service: $ServiceUrl)" -ForegroundColor DarkGray

try {
    Write-Section "Health"
    $health = Invoke-RestMethod -Uri $EndpointHealth -Method GET

    $healthLine = "status=$($health.status)  device=$($health.device)  dims=$($health.dimensions)  model=$($health.model)"
    if ($null -ne $health.execution_devices) {
        $healthLine += "  execution_devices=$($health.execution_devices -join ',')"
    }
    Write-Host $healthLine

    Assert-True "health status ok" ($health.status -eq "ok")
}
catch {
    Write-Result "FAIL" "health request failed: $($_.Exception.Message)"
    Write-Host ""
    Write-Host "=== Aborted ===" -ForegroundColor Red
    return
}

$detThreshold = 0.9999
if ($null -ne $health.execution_devices -and $health.execution_devices.Count -gt 0) {
    $execJoined = ($health.execution_devices -join ",").ToUpperInvariant()
    if ($execJoined.Contains("GPU")) {
        $detThreshold = 0.90
    }
    elseif ($execJoined.Contains("NPU")) {
        $detThreshold = 0.95
    }
}

Write-Section "Resource Recorder"
try {
    $script:UtilRecorder = Start-UtilizationRecorder 1000
    Write-Result "PASS" "utilization recording started"
}
catch {
    Write-Result "WARN" "could not start utilization recording: $($_.Exception.Message)"
}

Write-Section "Determinism"
try {
    $textDet = "Maschinelles Lernen ist spannend"
    $a = Get-Embedding $textDet
    $b = Get-Embedding $textDet
    $simDet = Get-CosineSimilarity $a.embedding $b.embedding
    Assert-Similar "same input => stable" $simDet $detThreshold
}
catch {
    Write-Result "FAIL" "determinism test threw: $($_.Exception.Message)"
}

Write-Section "Determinism Series (10x)"
try {
    $seriesText = "Maschinelles Lernen ist spannend"
    $seriesVectors = @()
    for ($i = 0; $i -lt 10; $i++) {
        $seriesVectors += ,(Get-Embedding $seriesText).embedding
    }

    $seriesSims = @()
    for ($i = 1; $i -lt $seriesVectors.Count; $i++) {
        $seriesSims += (Get-CosineSimilarity $seriesVectors[0] $seriesVectors[$i])
    }

    $simMin = ($seriesSims | Measure-Object -Minimum).Minimum
    $simAvg = ($seriesSims | Measure-Object -Average).Average
    $simMax = ($seriesSims | Measure-Object -Maximum).Maximum

    Write-Host ("determinism(10x) min={0} avg={1} max={2}" -f ([Math]::Round($simMin, 4)), ([Math]::Round($simAvg, 4)), ([Math]::Round($simMax, 4)))

    $seriesMinThreshold = [Math]::Max(0.85, $detThreshold - 0.02)
    Assert-Similar "determinism series min" $simMin $seriesMinThreshold
    Assert-Similar "determinism series avg" $simAvg $detThreshold
}
catch {
    Write-Result "FAIL" "determinism series threw: $($_.Exception.Message)"
}

Write-Section "Typos Robustness"
try {
    $typoA = Get-Embedding "Maschinelles Lernen ist spannend"
    $typoB = Get-Embedding "Maschinelles Lernen ist spennand"
    $simTypo = Get-CosineSimilarity $typoA.embedding $typoB.embedding
    Assert-Similar "same sentence with typo" $simTypo 0.90
}
catch {
    Write-Result "FAIL" "typo robustness test threw: $($_.Exception.Message)"
}

Write-Section "Semantic Similarity"
try {
    $q   = Get-Embedding "What is the capital of France?"
    $p1  = Get-Embedding "Which city is the capital of France?"
    $p2  = Get-Embedding "Paris is the capital city of France."
    $neg = Get-Embedding "The weather today is sunny and warm."

    $simPara  = Get-CosineSimilarity $q.embedding $p1.embedding
    $simFact  = Get-CosineSimilarity $q.embedding $p2.embedding
    $simUnrel = Get-CosineSimilarity $q.embedding $neg.embedding

    Assert-Similar    "question vs paraphrase"  $simPara  0.80
    Assert-Similar    "question vs factual ans" $simFact  0.75
    Assert-Dissimilar "question vs unrelated"   $simUnrel 0.70
}
catch {
    Write-Result "FAIL" "semantic similarity test threw: $($_.Exception.Message)"
}

Write-Section "Normalization"
try {
    $vec = $a.embedding
    $norm = Get-L2Norm $vec
    $normOk = [Math]::Abs($norm - 1.0) -lt 0.02
    Assert-True "L2 norm ~= 1.0" $normOk "(actual=$([Math]::Round($norm,6)))"
}
catch {
    Write-Result "FAIL" "normalization test threw: $($_.Exception.Message)"
}

Write-Section "Dimensions"
try {
    $dimOk = ($a.dimensions -eq 1024) -and ($a.embedding.Count -eq 1024)
    Assert-True "dimensions=1024 and vector.length=1024" $dimOk "(dims=$($a.dimensions) len=$($a.embedding.Count))"
}
catch {
    Write-Result "FAIL" "dimension test threw: $($_.Exception.Message)"
}

Write-Section "Short / Edge Inputs"
foreach ($inputText in @("ok", ".", "ä", "12345")) {
    try {
        $short = Get-Embedding $inputText
        $ok = ($short.dimensions -eq 1024) -and ($short.embedding.Count -eq 1024)
        Assert-True "short input '$inputText'" $ok "(dims=$($short.dimensions))"
    }
    catch {
        Write-Result "FAIL" "short input '$inputText' threw: $($_.Exception.Message)"
    }
}

Write-Section "Long Input (stress)"
try {
    $longText = ("Das maschinelle Lernen ist ein Teilgebiet der künstlichen Intelligenz. " * 40).Trim()
    $long = Get-Embedding $longText
    $ok = ($long.dimensions -eq 1024) -and ($long.embedding.Count -eq 1024)
    Assert-True "long input (~$($longText.Length) chars)" $ok "(dims=$($long.dimensions))"
}
catch {
    Write-Result "FAIL" "long input threw: $($_.Exception.Message)"
}

Write-Section "Multilingual"
try {
    $de = Get-Embedding "Künstliche Intelligenz verändert die Welt."
    $en = Get-Embedding "Artificial intelligence is changing the world."
    $simMl = Get-CosineSimilarity $de.embedding $en.embedding
    Assert-Similar "DE vs EN same meaning" $simMl 0.70
}
catch {
    Write-Result "FAIL" "multilingual test threw: $($_.Exception.Message)"
}

Write-Section "Requested vs Execution Device"
try {
    if ($null -ne $health.execution_devices -and $health.execution_devices.Count -gt 0) {
        $requested = [string]$health.device
        $actual = ($health.execution_devices -join ",")
        Write-Host "requested=$requested  actual=$actual"
        Assert-WarnIf "requested device differs from actual execution device" ($requested -ne $actual)
    }
    else {
        Write-Result "WARN" "health payload does not expose execution_devices"
    }
}
catch {
    Write-Result "FAIL" "device inspection threw: $($_.Exception.Message)"
}

Write-Section "Throughput (10 requests)"
try {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    for ($i = 0; $i -lt 10; $i++) {
        $null = Get-Embedding "Throughput test sentence number $i"
    }
    $sw.Stop()
    $rps = [Math]::Round(10 / $sw.Elapsed.TotalSeconds, 2)
    Write-Host "10 requests in $([Math]::Round($sw.Elapsed.TotalSeconds,2))s  =>  $rps req/s"
}
catch {
    Write-Result "FAIL" "throughput test threw: $($_.Exception.Message)"
}

Write-Section "Mathematical Equivalence"

try {
    $m1 = Get-Embedding "2 + 2"
    $m2 = Get-Embedding "1 + 3"
    $m3 = Get-Embedding "8 / 2"
    $m4 = Get-Embedding "sqrt(16)"
    $m5 = Get-Embedding "2 + 3"
    $m6 = Get-Embedding "The weather is sunny"

    $sim12 = Get-CosineSimilarity $m1.embedding $m2.embedding
    $sim13 = Get-CosineSimilarity $m1.embedding $m3.embedding
    $sim14 = Get-CosineSimilarity $m1.embedding $m4.embedding
    $sim15 = Get-CosineSimilarity $m1.embedding $m5.embedding
    $sim16 = Get-CosineSimilarity $m1.embedding $m6.embedding

    Assert-Similar    "2+2 vs 1+3"        $sim12 0.75
    Assert-Similar    "2+2 vs 8/2"        $sim13 0.65
    Assert-Similar    "2+2 vs sqrt(16)"   $sim14 0.55
    Assert-Dissimilar "2+2 vs 2+3"        $sim15 0.95
    Assert-Dissimilar "2+2 vs unrelated"  $sim16 0.50

    Write-Host "Info: same-result math equivalence is harder than plain paraphrase."
}
catch {
    Write-Result "FAIL" "mathematical equivalence test threw: $($_.Exception.Message)"
}

Write-Section "Technical Domain Similarity"
try {
    $t1 = Get-Embedding "OpenVINO compile failed on NPU because upper bounds are not specified."
    $t2 = Get-Embedding "The NPU compiler failed because the model used dynamic shapes without upper bounds."
    $t3 = Get-Embedding "ChromaDB stores vector embeddings for semantic retrieval."
    $t4 = Get-Embedding "Motorcycle riding through mountain roads is relaxing."

    $sim12 = Get-CosineSimilarity $t1.embedding $t2.embedding
    $sim13 = Get-CosineSimilarity $t1.embedding $t3.embedding
    $sim14 = Get-CosineSimilarity $t1.embedding $t4.embedding

    Assert-Similar    "same NPU/OpenVINO issue"     $sim12 0.78
    Assert-Dissimilar "NPU compile vs vector DB"    $sim13 0.85
    Assert-Dissimilar "NPU compile vs motorcycle"   $sim14 0.60
}
catch {
    Write-Result "FAIL" "technical domain test threw: $($_.Exception.Message)"
}

Write-Section "Near-Miss Distinction"
try {
    $n1 = Get-Embedding "embedding dimension is 1024"
    $n2 = Get-Embedding "sequence length is 1024"
    $n3 = Get-Embedding "embedding dimension is 1024"
    $n4 = Get-Embedding "embedding dimension is 512"

    $sim12 = Get-CosineSimilarity $n1.embedding $n2.embedding
    $sim13 = Get-CosineSimilarity $n1.embedding $n3.embedding
    $sim14 = Get-CosineSimilarity $n1.embedding $n4.embedding

    Assert-Similar    "same statement exact"              $sim13 0.99
    Assert-Dissimilar "embedding dim vs sequence length"  $sim12 0.97
    Assert-Dissimilar "1024 vs 512 dimensions"            $sim14 0.97
}
catch {
    Write-Result "FAIL" "near-miss test threw: $($_.Exception.Message)"
}

Write-Section "Paragraph Retrieval"
try {
    $p1 = Get-Embedding @"
The embedding service uses OpenVINO with a Qwen3 embedding model.
The model is reshaped to a fixed input length of 512 tokens to avoid dynamic-shape issues on the NPU.
The resulting embedding vector has 1024 dimensions and is L2-normalized.
"@

    $p2 = Get-Embedding @"
A Qwen3 embedding model is executed through OpenVINO on the Intel NPU.
To prevent compile problems, the model input is fixed to 512 tokens.
Each generated embedding is a normalized 1024-dimensional vector.
"@

    $p3 = Get-Embedding @"
The home server runs multiple virtual machines for infrastructure services.
Storage is separated from compute, and network traffic is routed through dedicated gateways.
System reliability is improved with redundancy and backups.
"@

    $sim12 = Get-CosineSimilarity $p1.embedding $p2.embedding
    $sim13 = Get-CosineSimilarity $p1.embedding $p3.embedding

    Assert-Similar    "same paragraph meaning" $sim12 0.85
    Assert-Dissimilar "embedding paragraph vs infrastructure" $sim13 0.80
}
catch {
    Write-Result "FAIL" "paragraph retrieval test threw: $($_.Exception.Message)"
}

Write-Section "Structured vs Natural Language"
try {
    $s1 = Get-Embedding "Model: Qwen3-Embedding-0.6B-fp16-ov. Device: NPU. Dimensions: 1024."
    $s2 = Get-Embedding "The Qwen3 embedding model runs on the NPU and produces 1024-dimensional vectors."
    $s3 = Get-Embedding '{"model":"Qwen3-Embedding-0.6B-fp16-ov","device":"NPU","dims":1024}'

    $sim12 = Get-CosineSimilarity $s1.embedding $s2.embedding
    $sim13 = Get-CosineSimilarity $s1.embedding $s3.embedding

    Assert-Similar "structured text vs natural language" $sim12 0.75
    Assert-Similar "structured text vs json-like form"   $sim13 0.70
}
catch {
    Write-Result "FAIL" "structured language test threw: $($_.Exception.Message)"
}

Write-Section "Code / Config Semantics"
try {
    $c1 = Get-Embedding 'model.reshape({"input_ids":[1,512],"attention_mask":[1,512]})'
    $c2 = Get-Embedding "The model input is reshaped to a fixed sequence length of 512 tokens."
    $c3 = Get-Embedding 'EMBEDDING_DIMS = 1024'
    $c4 = Get-Embedding "The output vector contains 1024 embedding dimensions."

    $sim12 = Get-CosineSimilarity $c1.embedding $c2.embedding
    $sim34 = Get-CosineSimilarity $c3.embedding $c4.embedding

    Assert-Similar "reshape code vs explanation"     $sim12 0.70
    Assert-Similar "config constant vs explanation"  $sim34 0.75
}
catch {
    Write-Result "FAIL" "code/config semantics test threw: $($_.Exception.Message)"
}

Write-Section "Top-k Mini Retrieval"
try {
    $docs = @(
        @{ id = "D1"; text = "OpenVINO NPU compile fails when model inputs have dynamic shapes without fixed bounds." },
        @{ id = "D2"; text = "Fix for NPU compile issue: reshape input_ids and attention_mask to a fixed sequence length like 512." },
        @{ id = "D3"; text = "The embedding service returns normalized vectors with 1024 dimensions." },
        @{ id = "D4"; text = "ChromaDB stores embedding vectors and supports similarity search for retrieval." },
        @{ id = "D5"; text = "The weather is sunny and warm today with light wind." },
        @{ id = "D6"; text = "A football team won the championship after extra time." },
        @{ id = "D7"; text = "Paris is the capital city of France." },
        @{ id = "D8"; text = "PowerShell backups can be automated with scheduled tasks and retention policies." },
        @{ id = "D9"; text = "With AUTO device, OpenVINO may execute embedding inference on GPU when available." },
        @{ id = "D10"; text = "PostgreSQL stores relational data in tables with indexes and transactions." }
    )

    $query = "How can I fix OpenVINO NPU compile errors caused by dynamic input shapes for an embedding model?"
    $queryVec = (Get-Embedding $query).embedding

    $scored = foreach ($doc in $docs) {
        $docVec = (Get-Embedding $doc.text).embedding
        $score = Get-CosineSimilarity $queryVec $docVec
        [PSCustomObject]@{
            id    = $doc.id
            score = $score
            text  = $doc.text
        }
    }

    $ranked = $scored | Sort-Object -Property score -Descending
    $top5 = $ranked | Select-Object -First 5

    Write-Host "Top-5 for query: $query"
    foreach ($item in $top5) {
        Write-Host ("  {0}: sim={1} | {2}" -f $item.id, ([Math]::Round($item.score, 4)), $item.text)
    }

    $top1 = ($ranked | Select-Object -First 1).id
    $top3Ids = @($ranked | Select-Object -First 3 | ForEach-Object { $_.id })

    Assert-True "top-1 is OpenVINO/NPU related" ($top1 -in @("D1", "D2", "D9")) "(top1=$top1)"
    Assert-True "top-3 contains dynamic-shape fix doc" ($top3Ids -contains "D2") "(top3=$($top3Ids -join ','))"
    Assert-True "top-3 excludes unrelated weather doc" (-not ($top3Ids -contains "D5")) "(top3=$($top3Ids -join ','))"
}
catch {
    Write-Result "FAIL" "top-k mini retrieval test threw: $($_.Exception.Message)"
}

Write-Section "Negation / Contradiction"
try {
    $negA = Get-Embedding "The embedding dimension is 1024"
    $negB = Get-Embedding "The embedding dimension is not 1024"

    $simNeg = Get-CosineSimilarity $negA.embedding $negB.embedding
    Assert-Dissimilar "positive vs negated statement" $simNeg 0.85
}
catch {
    Write-Result "FAIL" "negation/contradiction test threw: $($_.Exception.Message)"
}

Write-Section "Numeric / Version Sensitivity"
try {
    $numA = Get-Embedding "embedding dimension is 1024"
    $numB = Get-Embedding "embedding dimension is 512"
    $verA = Get-Embedding "OpenVINO version 2026.0"
    $verB = Get-Embedding "OpenVINO version 2025.4"

    $simDim = Get-CosineSimilarity $numA.embedding $numB.embedding
    $simVer = Get-CosineSimilarity $verA.embedding $verB.embedding

    Assert-Dissimilar "1024 vs 512 dimension" $simDim 0.97
    Assert-Dissimilar "version difference" $simVer 0.95
}
catch {
    Write-Result "FAIL" "numeric/version sensitivity test threw: $($_.Exception.Message)"
}

Write-Section "Logical Equivalence"
try {
    $logA = Get-Embedding "All models require fixed input shapes for NPU compilation"
    $logB = Get-Embedding "NPU compilation fails if input shapes are dynamic"

    $simLog = Get-CosineSimilarity $logA.embedding $logB.embedding
    Assert-Similar "logical implication equivalence" $simLog 0.80
}
catch {
    Write-Result "FAIL" "logical equivalence test threw: $($_.Exception.Message)"
}

Write-Section "Retrieval Edge Cases"
try {
    $queryEdge = Get-Embedding "How to fix OpenVINO NPU dynamic shape error"
    $edgeDocs = @(
        "Fix OpenVINO NPU error by reshaping inputs to static dimensions",
        "Dynamic input shapes cause compile failure on Intel NPU",
        "Use cosine similarity for vector search in databases",
        "Motorcycle engines require proper lubrication"
    )

    $edgeResults = @()
    foreach ($doc in $edgeDocs) {
        $emb = Get-Embedding $doc
        $sim = Get-CosineSimilarity $queryEdge.embedding $emb.embedding
        $edgeResults += [PSCustomObject]@{
            text = $doc
            sim  = $sim
        }
    }

    $edgeTop = $edgeResults | Sort-Object sim -Descending
    $edgeTop | Select-Object -First 3 | ForEach-Object {
        Write-Host ("sim=" + [Math]::Round($_.sim, 4) + " | " + $_.text)
    }

    $worstEdge = $edgeResults | Sort-Object sim | Select-Object -First 1
    Assert-True "retrieval edge excludes obvious unrelated at top" ($worstEdge.text -like "*Motorcycle*") "(lowest='$($worstEdge.text)')"
}
catch {
    Write-Result "FAIL" "retrieval edge cases test threw: $($_.Exception.Message)"
}

Write-Section "Noise Robustness"
try {
    $noiseA = Get-Embedding "OpenVINO NPU compile error"
    $noiseB = Get-Embedding "!!! OpenVINO ### NPU ??? compile error !!!"

    $simNoise = Get-CosineSimilarity $noiseA.embedding $noiseB.embedding
    Assert-Similar "noise robustness" $simNoise 0.80
}
catch {
    Write-Result "FAIL" "noise robustness test threw: $($_.Exception.Message)"
}

Write-Section "Code vs Explanation (Deep)"
try {
    $deepCode = Get-Embedding "model.reshape({'input_ids':[1,512],'attention_mask':[1,512]})"
    $deepText = Get-Embedding "Fix OpenVINO NPU error by setting static input shape to 512"

    $simDeep = Get-CosineSimilarity $deepCode.embedding $deepText.embedding
    Assert-Similar "code vs explanation mapping" $simDeep 0.70
}
catch {
    Write-Result "FAIL" "code vs explanation (deep) test threw: $($_.Exception.Message)"
}

Write-Section "Liara Context Test"
try {
    $liq = Get-Embedding "How does Liara decide which database to use?"
    $lid1 = Get-Embedding "The orchestrator decides use case and the memory service selects the database"
    $lid2 = Get-Embedding "Redis is used for short-term memory and caching"
    $lid3 = Get-Embedding "Motorcycle cornering requires body positioning"

    Assert-Similar "core architecture match" (Get-CosineSimilarity $liq.embedding $lid1.embedding) 0.75
    Assert-Similar "related knowledge" (Get-CosineSimilarity $liq.embedding $lid2.embedding) 0.60
    Assert-Dissimilar "unrelated topic" (Get-CosineSimilarity $liq.embedding $lid3.embedding) 0.50
}
catch {
    Write-Result "FAIL" "liara context test threw: $($_.Exception.Message)"
}

$utilSamples = @()
try {
    $utilSamples = Stop-UtilizationRecorder $script:UtilRecorder
}
catch {
    Write-Result "WARN" "failed to stop utilization recorder cleanly: $($_.Exception.Message)"
}

Show-UtilizationReport $utilSamples

Write-Host ""
Write-Host "=== Summary ===" -ForegroundColor Magenta
Write-Host "PASS=$script:PassCount  WARN=$script:WarnCount  FAIL=$script:FailCount"

if ($script:FailCount -gt 0) {
    Write-Host "Result: FAILURES DETECTED" -ForegroundColor Red
} elseif ($script:WarnCount -gt 0) {
    Write-Host "Result: PASS WITH WARNINGS" -ForegroundColor Yellow
} else {
    Write-Host "Result: ALL TESTS PASSED" -ForegroundColor Green
}