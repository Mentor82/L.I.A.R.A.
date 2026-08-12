# NPU Helper Service

## Service Profil

Jeder Helper ist:

```text
- Windows C++ Prozess (LiaraHelperInferServer.exe)
- OpenVINO Runtime
- 2 Pipeline-DLLs zur Laufzeit geladen (liara_instruct.dll + liara_coder.dll)
- netzwerkfaehig (HTTP, 127.0.0.1 only)
- stateless
```

## Verzeichnisstruktur

```text
liara-helper/
  LiaraHelperInferServer.exe   <- Server, kein direktes openvino_genai Link
  plugins/
    liara_instruct.dll          <- wraps Qwen2.5-1B-Instruct-fp16-test-ov
    liara_coder.dll             <- wraps Qwen2.5-Coder-0.5B-fp16-test-ov
    pipeline_dll.cpp            <- gemeinsame Quelldatei beider DLLs

liara-inference/common/
  pipeline_plugin.hpp           <- C ABI Schnittstelle + PipelinePlugin Loader
```

## Aktueller Contract

```text
Pflichtprofile:
- Instruct (liara_instruct.dll, REQUIRED)
- Coder    (liara_coder.dll, optional – Server startet auch ohne)

Pflichtzustand:
- beide Profile bereit
- beide Profile warm (kein Reload pro Request)
```

```text
Routing:
- quick_extract -> Instruct
- code_*       -> Coder (System-Prompt), Pipeline: aktuell Instruct
```

```text
Runtime-Metriken:
- warm_age_ms
- reload_count
```

## Start-Kommando

```powershell
$env:PATH = "C:\ai\Liara-NPU-Client\runtime;$env:PATH"
.\LiaraHelperInferServer.exe `
    --port=8765 `
    --device=CPU `
    --models-dir=C:\ai\models\OpenVINO `
    --plugins-dir=.\plugins
```

`--plugins-dir` ist optional; default ist `plugins\` relativ zur exe.

## Pipeline-DLL C ABI

```cpp
extern "C" {
    LiaraPipelineHandle liara_pipeline_create(const char* model_dir, const char* device);
    void                liara_pipeline_destroy(LiaraPipelineHandle h);
    int                 liara_pipeline_generate(LiaraPipelineHandle h,
                                                const char* prompt, int max_tokens,
                                                char* out_buf, int out_buf_size,
                                                double* out_ms);
    const char*         liara_pipeline_label(LiaraPipelineHandle h);
}
```

Siehe `liara-inference/common/pipeline_plugin.hpp` fuer die vollstaendige Spezifikation
inkl. RAII-Loader `PipelinePlugin` fuer den Server.

## Betriebsverhalten

```text
Beim Start:
1. DLL liara_instruct.dll laden (LoadLibrary)
2. liara_pipeline_create() aufrufen -> Modell laden (blockend)
3. DLL liara_coder.dll laden (optional, Fehler = WARNING, kein Abbruch)
4. Server beginnt zu lauschen (127.0.0.1:<port>)
```

```text
Fehlerbehandlung:
- fehlende liara_instruct.dll   -> FATAL, exit 1
- fehlende liara_coder.dll      -> WARNING, nur Instruct verfuegbar
- Modell-Verzeichnis fehlt      -> FATAL fuer Instruct, WARNING fuer Coder
```

## OpenVINO Libraries (Intel AI Boost / NPU)

### Muss (fuer C++ Runtime)

```text
openvino.dll
openvino_intel_npu_plugin.dll
openvino_intel_cpu_plugin.dll (Fallback)
tbb12.dll
tbbmalloc.dll
```

### Tokenizer/GenAI (nur in den DLLs, nicht im Server selbst)

```text
openvino_tokenizers.dll
openvino_genai.dll
```

Diese DLLs werden nur von `liara_instruct.dll` / `liara_coder.dll` benoetigt,
nicht vom Server-Prozess direkt.

### Empfehlung

```text
Bevorzugtes Modellformat: OpenVINO IR (.xml + .bin)
-> reduziert Abhaengigkeit von Frontend-DLLs
-> stabilere Runtime-Verteilung auf Worker
```
