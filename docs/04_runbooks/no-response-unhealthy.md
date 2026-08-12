# Runbook: Keine Antwort oder Service unhealthy

Stand: 2026-05-01

## Symptom

- Frontend bekommt keine Antwort.
- `POST /chat` oder `POST /chat/stream` haengt oder bricht ab.
- `GET /health` oder `GET /health/backends` meldet Fehler.
- Memory-/Embedding-/Inference-Pfad liefert leere oder degradierte Ergebnisse.

## Schnellcheck

```powershell
curl http://127.0.0.1:8010/health
curl http://127.0.0.1:8010/health/backends
curl http://127.0.0.1:8020/health
curl http://127.0.0.1:8030/health
```

Wenn Docker verwendet wird:

```powershell
docker compose ps
docker compose logs liara-api --tail=100
docker compose logs liara-memory --tail=100
```

Embedding hat keinen Compose-Logstream. Den externen Host-/IP-Prozess direkt pruefen.

## Typische Ursachen

| Bereich | Ursache | Pruefung |
| --- | --- | --- |
| API | Prozess laeuft nicht auf Port 8010 | `curl /health` |
| Memory | `MEMORY_SERVICE_BASE_URL` falsch oder Memory unhealthy | `curl :8020/health/backends` |
| Embedding | Modellpfad fehlt oder OpenVINO-Fallback aktiv | `curl :8030/health` |
| llama.cpp | Primaerer Inference-Pfad nicht erreichbar | `curl http://127.0.0.1:8000/health` oder llama.cpp Serverprozess pruefen |
| Ollama | Fallback-/Alternativmodell nicht erreichbar | `curl http://127.0.0.1:11434/api/tags` |
| Redis/Postgres/Qdrant/Neo4j | Compose-Backend nicht healthy | `docker compose ps` |
| laufender Altprozess | Code geaendert, Prozess nicht neu gestartet | Prozess stoppen und neu starten |
| Sandbox/Tool | Tool blockiert Pfad oder Befehl | Sys-Audit-Endpunkte pruefen |

## Minimaler lokaler Start

Infrastruktur:

```powershell
docker compose up -d liara-postgres liara-redis liara-qdrant liara-neo4j liara-chroma liara-validator
```

Lokale Kernprozesse:

```powershell
.\.venv\Scripts\python.exe scripts\service_guard.py start --service memory --repo-root C:\ai\LIARA
.\.venv\Scripts\python.exe scripts\service_guard.py start --service embedding --repo-root C:\ai\LIARA
.\.venv\Scripts\python.exe scripts\service_guard.py start --service api --repo-root C:\ai\LIARA
.\.venv\Scripts\python.exe scripts\service_guard.py start --service bridge --repo-root C:\ai\LIARA
```

API und Memory sollen im lokalen Betrieb nicht ueber Docker Compose laufen,
weil der Containerpfad bei Host-/NPU-/Bridge-Verbindungen abweichen kann.

Alternative Einzelstarts ohne Guard:

```powershell
python -m uvicorn services.memory.app:app --host 127.0.0.1 --port 8020
python -m uvicorn services.api.app:app --host 127.0.0.1 --port 8010
```

Embedding separat lokal:

```powershell
src\emeddingserver\build-msvc-clean\LiaraEmbeddingService.exe --port=8030
```

Python nur als Fallback/Wrapper:

```powershell
python -m uvicorn services.embedding.app:create_embedding_service_app --factory --host 127.0.0.1 --port 8030
```

## Reihenfolge beim Debuggen

1. API Health pruefen.
2. Backend Health pruefen.
3. Memory direkt pruefen.
4. Embedding direkt pruefen.
5. Primaeren Inferenzprovider `llama.cpp` pruefen.
6. Falls Fallback/Hybrid aktiv ist, Ollama/OpenVINO pruefen.
7. Logs der betroffenen Komponente lesen.
8. Nach Codeaenderungen laufende API-/Memory-/Embedding-/llama.cpp-Prozesse neu starten.

## Bekannte Besonderheiten

- Compose setzt fuer `liara-memory` `EMBEDDING_SERVICE_BASE_URL=http://host.docker.internal:8030`. Das erwartet einen erreichbaren externen Host-/IP-Embedding-Service. Embedding wird nicht durch Docker Compose gestartet.
- Der primaere Inference-Pfad ist aktuell `llama.cpp` ueber den lokalen nativen SYCL-Pfad.
- Compose setzt fuer `liara-api` zusaetzlich `OLLAMA_HOST=host.docker.internal` und `OLLAMA_PORT=11434`. Ollama muss nur dann auf dem Host laufen, wenn der Fallback-/Alternativpfad genutzt wird.
- `docs/LIARA_SNAPSHOT_2026-04-28.md` weist darauf hin, dass fokussierte Smoke-Tasks unter Windows verlaesslicher sind als ein grosser Sammel-Smoke.
