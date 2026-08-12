# Embeddingserver-Kohaerenz zum Python-Embedding-Worker

Stand: 2026-05-01

Verglichen wurden:

- C++: `src/emeddingserver`
- Python HTTP-Service: `services/embedding/app.py`, `services/embedding/engine.py`
- Python Redis-Worker: `workers/embedding-worker/worker.py`
- Contract: `services/contracts/service_boundaries.py`

## Ergebnis

Der C++-Embeddingserver ist nach den Anpassungen auf den zentralen `MemoryEmbeddingResponse`- und `MemoryHealthResponse`-Contract ausgerichtet.

## Gemeinsamer Request-Contract

Beide Pfade verwenden:

```json
{
  "input_text": "text",
  "model": "optional-model-name",
  "normalize": true,
  "metadata": {}
}
```

`input_text`, `normalize` und `metadata` sind in beiden Pfaden relevant. Der C++-Dienst respektiert jetzt auch das optionale `model`-Feld im Response-Item, wie der Python-Service.

## Gemeinsamer Response-Contract

Beide Pfade liefern:

```json
{
  "item": {
    "model": "...",
    "dimensions": 1024,
    "vector": [],
    "metadata": {}
  },
  "status": {
    "status": "success|partial|failed",
    "backend": "embedding",
    "degraded": false,
    "error": null,
    "metadata": {}
  }
}
```

Der C++-Dienst setzt `status.error` bei Erfolg jetzt auf `null`, nicht auf einen leeren String.

## Health-Contract

Der Python-Service nutzt `MemoryHealthResponse`. Der C++-Dienst liefert jetzt denselben Kernrahmen:

```json
{
  "status": {
    "status": "success|partial|failed",
    "backend": "embedding",
    "degraded": true,
    "error": "...",
    "metadata": {}
  },
  "backend_health": {
    "embedding": "healthy|degraded|unavailable"
  },
  "device": "...",
  "execution_devices": [],
  "model": "...",
  "dimensions": 1024,
  "runtime_backend": "openvino-cpp",
  "effective_max_length": 512,
  "configured_model_id": "...",
  "configured_model_dir": "..."
}
```

Zusaetzliche alte C++-Felder wie `ready`, `runtime`, `dims`, `linep` bleiben fuer native Diagnose erhalten.

## Tokenizer-Kohaerenz

Vorher konnte der C++-Dienst ohne `openvino_tokenizer.xml` zero-filled Input-Tensoren verwenden. Das war fuer Smoke-Tests bequem, aber semantisch nicht kohaerent zum Python-Worker, weil verschiedene Texte dann nicht korrekt tokenisiert werden.

Aktueller Stand:

- Wenn das OpenVINO-Modell Token-Inputs erwartet, ist `openvino_tokenizer.xml` Pflicht.
- Wenn das Modell direkte String-Inputs akzeptiert, darf der C++-Dienst ohne separaten Tokenizer arbeiten.
- Zero-filled Token-Tensoren werden fuer echte Embedding-Anfragen nicht mehr verwendet.

## Bekannte Unterschiede

- Python HTTP-Service hat TTL/LRU-Cache und Runtime-Statistiken mit echten Countern; C++ liefert aktuell kompatible Health-Felder, aber noch keine echten Request-Counter.
- Python kann auf Transformers fallbacken; C++ fallbackt aktuell nur auf CPU innerhalb OpenVINO. Der Python-Fallback bleibt damit weiterhin der degradierte Pfad ausserhalb des C++-Hot-Path.
- Python Redis-Worker begrenzt `max_input_chars`; C++ verlaesst sich auf Tokenizer/Model-`max_seq_len`.
- C++ hat zusaetzlich LiNeP fuer Scheduler-Embedding; Python bleibt API/Queue-orientiert.

## Verifikation

```text
VC++ Direktbuild: OK
LiaraEmbeddingService.exe --help: OK
GET /health mit ungueltigem Modell: HTTP 503 + MemoryHealthResponse-kompatibler Body
```
