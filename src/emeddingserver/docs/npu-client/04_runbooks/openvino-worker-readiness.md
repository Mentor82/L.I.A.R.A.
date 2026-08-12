# Runbook: OpenVINO Worker Readiness

## Trigger

Worker soll gestartet oder redeployed werden.

## Ziel

Nur starten, wenn Runtime-DLLs und Ziel-Device reproduzierbar funktionieren.

## Preconditions

- Modell liegt als OpenVINO IR vor (`.xml` + `.bin`).
- `openvino_probe` ist gebaut (`build/openvino_probe.exe`).
- `liara_instruct.dll` und `liara_coder.dll` sind gebaut (`liara-helper/plugins/`).
- `LiaraHelperInferServer.exe` ist gebaut (`liara-helper/`).
- Device ist explizit gesetzt (`npu` oder `cpu`, nie auto).
- Build wurde ueber `build.ps1` mit MSVC-Toolchain ausgefuehrt.
- OpenVINO Runtime DLLs liegen in `runtime/` oder sind per `PATH` erreichbar.

## Schritte

1. Strict runtime directory pruefen:

```text
openvino_probe <model.xml> --device=npu --runtime-dir=C:\deploy\openvino
```

1. Geteiltes Notebook-Profil verwenden (optional):

```text
openvino_probe <model.xml> --device=npu --profile=shared-notebook
```

1. Smoke-Inference aktivieren (optional, empfohlen):

```text
openvino_probe <model.xml> --device=npu --infer-smoke
```

1. Dynamische Modelle fuer Smoke ohne manuelles Export-IR pruefen:

```text
openvino_probe <model.xml> --device=npu --infer-smoke --smoke-seq-len=128
```

1. Bei ONNX-Einsatz ONNX-Frontend mitpruefen:

```text
openvino_probe <model.xml> <model.onnx> --device=npu --runtime-dir=C:\deploy\openvino
```

## Erfolgskriterien

- `Overall: PASS` beim openvino_probe-Lauf.
- Keine fehlenden required DLLs.
- `compile_model` auf dem gewaehlten Device erfolgreich.
- Falls `--infer-smoke` aktiv: `infer_smoke` ebenfalls erfolgreich.
- `GET /health` des LiaraHelperInferServer liefert `{"status":"ok","instruct_ready":true}`.

## Fehlerbehandlung

- Bei `Overall: FAIL` Worker-Start abbrechen.
- Fehlende DLLs laut Ausgabe nachdeployen.
- Bei Device-Fehlern auf alternativen, expliziten Device-Modus wechseln.
- Bei dynamischen Shape-Fehlern `--smoke-seq-len` verwenden oder statisches IR erzeugen.
