$ErrorActionPreference = 'Stop'

$oneApiBatCandidates = @()
if ($env:ONEAPI_SETVARS_BAT) { $oneApiBatCandidates += $env:ONEAPI_SETVARS_BAT }
$oneApiBatCandidates += 'C:\Program Files (x86)\Intel\oneAPI\setvars.bat'
$oneApiBat = $oneApiBatCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $oneApiBat) { throw 'oneAPI setvars.bat nicht gefunden. Setze ONEAPI_SETVARS_BAT.' }

$genAiSetupCandidates = @()
if ($env:OPENVINO_GENAI_SETUPVARS_BAT) { $genAiSetupCandidates += $env:OPENVINO_GENAI_SETUPVARS_BAT }
$genAiSetupCandidates += 'C:\openvino_genai\setupvars.bat'
$genAiSetup = $genAiSetupCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $genAiSetup) { throw 'openvino_genai setupvars.bat nicht gefunden. Setze OPENVINO_GENAI_SETUPVARS_BAT.' }

$genAiPyCandidates = @()
if ($env:OPENVINO_GENAI_PYTHON_DIR) { $genAiPyCandidates += $env:OPENVINO_GENAI_PYTHON_DIR }
$genAiPyCandidates += 'C:\openvino_genai\python'
$genAiPy = $genAiPyCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $genAiPy) { throw 'openvino_genai python dir nicht gefunden. Setze OPENVINO_GENAI_PYTHON_DIR.' }

if (-not $env:OPENVINO_TTS_ENABLED) { $env:OPENVINO_TTS_ENABLED = 'true' }
if (-not $env:OPENVINO_TTS_MODEL_DIR) { $env:OPENVINO_TTS_MODEL_DIR = 'C:\ai\models\OpenVINO\MiniCPM-o-2.6-int4-sym-cw-ov' }
if (-not $env:OPENVINO_TTS_MODE) { $env:OPENVINO_TTS_MODE = 'cpu_reference' }
if (-not $env:OPENVINO_TTS_SPEAKER_PROFILE) { $env:OPENVINO_TTS_SPEAKER_PROFILE = 'gentle-feminine-v1' }

if ($env:OPENVINO_TTS_ENABLED -match '^(1|true|yes|on)$') {
    if ($env:OPENVINO_TTS_MODE -ne 'cpu_reference') {
        throw 'OPENVINO_TTS_MODE muss bis zum bestandenen Static-Cache-Gate cpu_reference sein.'
    }
    $ttsManifest = Join-Path $env:OPENVINO_TTS_MODEL_DIR 'tts\runtime_manifest.json'
    $ttsSpeaker = Join-Path $env:OPENVINO_TTS_MODEL_DIR ('tts\speakers\' + $env:OPENVINO_TTS_SPEAKER_PROFILE + '.npy')
    if (-not (Test-Path $ttsManifest)) { throw ('TTS runtime manifest fehlt: ' + $ttsManifest) }
    if (-not (Test-Path $ttsSpeaker)) { throw ('TTS speaker profile fehlt: ' + $ttsSpeaker) }
}

Write-Host ('Loaded oneAPI env via ' + $oneApiBat)
Write-Host ('Loaded openvino_genai env via ' + $genAiSetup)
Write-Host ('Using OPENVINO_GENAI_PYTHON_DIR=' + $genAiPy)
Write-Host ('TTS enabled=' + $env:OPENVINO_TTS_ENABLED + ' mode=' + $env:OPENVINO_TTS_MODE + ' profile=' + $env:OPENVINO_TTS_SPEAKER_PROFILE)

$command = (
    '"' + $oneApiBat + '" >nul && ' +
    '"' + $genAiSetup + '" >nul && ' +
    'set "PYTHONPATH=' + $genAiPy + ';%PYTHONPATH%" && ' +
    'set "OPENVINO_TTS_ENABLED=' + $env:OPENVINO_TTS_ENABLED + '" && ' +
    'set "OPENVINO_TTS_MODEL_DIR=' + $env:OPENVINO_TTS_MODEL_DIR + '" && ' +
    'set "OPENVINO_TTS_MODE=' + $env:OPENVINO_TTS_MODE + '" && ' +
    'set "OPENVINO_TTS_SPEAKER_PROFILE=' + $env:OPENVINO_TTS_SPEAKER_PROFILE + '" && ' +
    '"c:/ai/LIARA/.venv/Scripts/python.exe" -m uvicorn services.inference.openvino_npu_app:app --host 127.0.0.1 --port 8040 --log-level info'
)

& cmd.exe /c $command
