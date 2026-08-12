# Plugin Host Service (DLL)

## Ziel

- modulare Erweiterbarkeit: Instruct und Coder als eigenstaendige DLLs
- keine statische Abhaengigkeit des Servers von `openvino_genai`
- Pipelines unabhaengig austauschbar, erweiterbar, auf verschiedenen Devices

## Grundprinzip

```text
LiaraHelperInferServer.exe
  |
  +-- LoadLibrary(liara_instruct.dll)  --> Qwen2.5-1B-Instruct
  +-- LoadLibrary(liara_coder.dll)     --> Qwen2.5-Coder-0.5B
```

Beide DLLs werden aus derselben Quelldatei `pipeline_dll.cpp` kompiliert.
Der Label ergibt sich aus dem Modell-Verzeichnisnamen.

## Implementiertes C ABI

Definiert in `liara-inference/common/pipeline_plugin.hpp`:

```cpp
extern "C" {
    // Erstellt und laedt eine Pipeline. Gibt nullptr bei Fehler zurueck.
    LiaraPipelineHandle liara_pipeline_create(const char* model_dir,
                                              const char* device);

    // Gibt die Pipeline und alle Ressourcen frei.
    void liara_pipeline_destroy(LiaraPipelineHandle h);

    // Fuehrt Inferenz durch. Gibt 0 bei Erfolg, -1 bei Fehler zurueck.
    int liara_pipeline_generate(LiaraPipelineHandle h,
                                const char* prompt,
                                int max_tokens,
                                char* out_buf, int out_buf_size,
                                double* out_ms);

    // Gibt einen lesbaren Label-String zurueck (Lebensdauer = Handle-Lebensdauer).
    const char* liara_pipeline_label(LiaraPipelineHandle h);
}
```

## RAII-Loader im Server

`pipeline_plugin.hpp` enthaelt zusaetzlich die Klasse `PipelinePlugin`,
die `LoadLibrary`, `GetProcAddress` und `liara_pipeline_create` kapselt:

```cpp
PipelinePlugin instruct;
instruct.load("plugins/liara_instruct.dll",
              "C:/ai/models/OpenVINO/Qwen2.5-1B-Instruct-fp16-test-ov",
              "CPU");
if (instruct.ready()) {
    double ms;
    std::string result = instruct.generate(prompt, 256, ms);
}
```

## Plugin-Typen (geplant)

```text
liara_instruct.dll    <- implementiert
liara_coder.dll       <- implementiert
liara_classifier.dll  <- geplant
liara_summarizer.dll  <- geplant
```

## Build (CMake)

```cmake
foreach(plugin_name IN ITEMS liara_instruct liara_coder)
    add_library(${plugin_name} SHARED liara-helper/plugins/pipeline_dll.cpp)
    target_compile_definitions(${plugin_name} PRIVATE LIARA_PIPELINE_EXPORT)
    # --> liara-helper/plugins/liara_instruct.dll
    # --> liara-helper/plugins/liara_coder.dll
endforeach()
```

## Vorteile gegenueber direktem Link

```text
Server-Binary benoetigt openvino_genai.dll nicht direkt
Pipelines koennen auf verschiedenen Devices laufen
Einzelne DLLs austauschbar ohne Rebuild des Servers
Feature-Flags (z.B. Coder optional) ohne Recompile
Rollback: alte DLL-Version zurueckkopieren
```
