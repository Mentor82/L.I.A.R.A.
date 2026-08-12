# Tests

Aktueller Verifikationsstand fuer den Ist-Zustand.

## Aktuelle Test-Binaerziele

- `build/heartbeat_demo.exe`
- `build/HelperContractTests.exe`
- `build/openvino_probe.exe` (wenn OpenVINO gefunden wurde)

## Build

```text
.\build.ps1
```

## Test-Kommandos

```text
.\build\heartbeat_demo.exe
.\build\HelperContractTests.exe
```

```text
.\build\openvino_probe.exe <model.xml> --device=npu --infer-smoke --smoke-seq-len=128
```

## Erwartete Ergebnisse

1. `heartbeat_demo`: Overall PASS
1. `HelperContractTests`: Overall PASS
1. `openvino_probe`: `compile_model` und optional `infer_smoke` PASS
