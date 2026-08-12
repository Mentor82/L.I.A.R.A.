# Service: liara-embedding

Stand: 2026-07-14  
Code: `services/embedding/`, `services/embedding_dev/`, `src/emeddingserver/`

## Aufgabe

Der Embedding-Service erzeugt Vektoren fuer Retrieval und Kontextsuche. Der aktuelle Zielpfad ist zweistufig:

1. Primaer: nativer C++ OpenVINO-Dienst `LiaraEmbeddingService.exe` unter `src/emeddingserver/`.
2. Fallback: Python-FastAPI-Service `services/embedding/app.py` mit lokaler OpenVINO/Transformers-Engine.

Der Python-Service kann als Wrapper laufen: `/embedding/generate` versucht zuerst `EMBEDDING_NATIVE_SERVICE_BASE_URL` und nutzt die Python-Engine nur dann, wenn der native Dienst nicht erreichbar ist, fehlerhaft antwortet oder wegen Selbstreferenz uebersprungen werden muss.

## Endpunkte

- `POST /embedding/generate`
- `GET /health`
- `GET /health/dev`

LiNeP (nativ, Scheduler-Pfad):

- `EMBED_REQUEST`/`EMBED_RESPONSE` (Port 8767)
- `CONSENSUS_REQUEST`/`CONSENSUS_RESPONSE` (Port 8767)

Beim Consensus-Scoring wird der gemeinsame Aufgabenkontext aus `task_type + source_text` als primaeres Grounding genutzt. Antwort-zu-Antwort-Konsistenz bleibt ein sekundares Signal und wird durch ein Context-Gate sowie eine Disagreement-Penalty begrenzt, damit semantisch aehnliche aber inhaltlich falsche Antworten nicht automatisch hohe Final-Scores erhalten.

Die Dev-Variante `services/embedding_dev/app.py` bietet:

- `GET /health`
- `POST /embedding/generate`

## Runtime

Embedding laeuft in LIARA nicht als Docker-Compose-Service. Der Dienst ist immer ein externer IP-/Host-Endpunkt, im aktuellen lokalen Betrieb typischerweise `127.0.0.1:8030`.

Python-Fallback/Wrapper:

- Prozess: lokaler Python-Prozess, nicht Compose
- Typischer Port: `8030`
- Default Backend: `openvino`
- Device: hostabhaengig, lokal bevorzugt `NPU`
- Fallback erlaubt: `EMBEDDING_ALLOW_FALLBACK=1`

Nativer Primaerpfad:

- Binary: `LiaraEmbeddingService.exe`
- Default Contract: `POST /embedding/generate`, `GET /health`
- Runtime: OpenVINO C++ Runtime, Device bevorzugt `NPU`
- LiNeP TCP/Heartbeat gemaess Contract: `8767` / `8768`

Wichtig: `EMBEDDING_NATIVE_SERVICE_BASE_URL` darf nicht auf denselben oeffentlichen Python-Service zeigen wie `EMBEDDING_SERVICE_BASE_URL`, sonst wuerde der Wrapper auf sich selbst zeigen. Der Python-Service erkennt diese Selbstreferenz und faellt dann als `partial/degraded` auf Python zurueck.

Wenn `liara-memory` in Docker Compose laeuft, zeigt `EMBEDDING_SERVICE_BASE_URL` auf den Host/IP-Endpunkt des externen Embedding-Dienstes, z. B. `http://host.docker.internal:8030`. Es gibt keinen Compose-Dienst `liara-embedding`.

## Konfiguration

Wichtige ENV-Werte:

- `EMBEDDING_MODEL_DIR`
- `EMBEDDING_MODEL_ID`
- `EMBEDDING_DEVICE`
- `EMBEDDING_BACKEND`
- `EMBEDDING_ALLOW_FALLBACK`
- `EMBEDDING_FALLBACK_DEVICE`
- `EMBEDDING_NATIVE_PRIMARY_ENABLED`
- `EMBEDDING_NATIVE_SERVICE_BASE_URL`
- `EMBEDDING_NATIVE_TIMEOUT_SECONDS`
- `EMBEDDING_MAX_LENGTH`
- `EMBEDDING_CACHE_*`
- `EMBEDDING_ALERT_*`

## Health-Metriken

Der Service fuehrt Runtime-Zaehler:

- Requests
- Fehler
- Cache-Hits
- degradierte/Fallback-Requests
- geschaetzte Truncations
- Backend-Wechsel
- Durchschnitts- und Maximal-Latenz

## Aktueller Befund

Embedding ist ein eigener Service und nicht nur eine interne Hilfsfunktion. Memory kann ueber `EMBEDDING_SERVICE_BASE_URL` darauf zugreifen. Im Compose-File zeigt `liara-memory` auf `http://host.docker.internal:8030`; das ist eine bewusste Host-Service-Kopplung auf einen extern laufenden Embedding-Prozess.

Aktueller Ist-Stand: Der native C++ EmbeddingServer existiert im LIARA-Repo unter `src/emeddingserver/`. Der Python-Service kann ihn als Primaerpfad anbinden und Python nur noch als Fallback/Degraded Path nutzen. Native Erfolge werden direkt im `MemoryEmbeddingResponse`-Contract zurueckgegeben; native Fehler fuehren nicht zum Request-Abbruch, sondern zu Python-Fallback mit `status=partial`, `degraded=true` und Metadaten `native_primary_error`/`python_fallback_used`.

Im Laufzeit-Snapshot vom 2026-07-14 lief das paketierte Binary unter
`workers/embedding/exec/` direkt auf Port 8030. Health meldete OpenVINO C++,
NPU, 1024 Dimensionen sowie aktives LiNeP mit Worker 30, TCP 8767 und
Heartbeat 8768. Das belegt den Embedding-Slot, nicht jedoch einen vollstaendig
an LIARA angebundenen globalen Ressourcen-Scheduler.
