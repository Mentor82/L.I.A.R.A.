# workers/embedding/exec

Deployment-Slot für den nativen C++ Embedding Service (`LiaraEmbeddingService`).

## Layout

```text
exec/
  bin/    <- LiaraEmbeddingService.exe (native OpenVINO C++ Runtime)
  lib/    <- OpenVINO Runtime DLLs und native Abhängigkeiten (manuell befüllen)
  conf/   <- embedding_config.toml (Laufzeit-Konfiguration)
  log/    <- Service-Logs (Laufzeit, nicht eingecheckt)
```

## Quelle

Der C++ Source liegt in `src/emeddingserver/`.  
Binary-Builds stammen aus `src/emeddingserver/build-msvc-manual/` bzw. dem NPU-Client (`LIARA-NPU-CLIENT`).

## Start

```powershell
# DLLs müssen im PATH oder in lib/ liegen
$env:PATH = "c:\ai\LIARA\workers\embedding\exec\lib;" + $env:PATH
& c:\ai\LIARA\workers\embedding\exec\bin\LiaraEmbeddingService.exe `
  --config c:\ai\LIARA\workers\embedding\exec\conf\embedding_config.toml
```

## Schnittstellen

| Protokoll | Port    | Verwendet von                        |
|-----------|---------|--------------------------------------|
| HTTP      | 8030    | Orchestrator / Memory Adapter / API  |
| LiNeP TCP | 8767    | Scheduler → Embedding-Slot           |
| LiNeP UDP | 8768    | Embedding-Slot → Scheduler Heartbeat |

## lib/ befüllen

Benötigte DLLs (OpenVINO Runtime, NPU Plugin, TBB):

```text
openvino.dll
openvino_c.dll
openvino_intel_npu_plugin.dll
tbb12.dll
```

Quellpfad je nach OpenVINO-Installation, z.B.:
`C:\Program Files (x86)\Intel\openvino_2025\runtime\bin\intel64\Release\`

## Verhältnis zum Python-Fallback

Der Python-Service `services/embedding/app.py` bleibt der aktive Fallback-Pfad.  
Sobald `EMBEDDING_NATIVE_PRIMARY_ENABLED=1` und `EMBEDDING_NATIVE_SERVICE_BASE_URL=http://127.0.0.1:8030`  
in `.env` gesetzt sind, routet der Python-Wrapper zuerst gegen diesen nativen Service.
