# Embedding Docker Status

Stand: 2026-05-02

## Status

Diese Datei ist bewusst als historische Notiz erhalten, aber der fruehere Ansatz ist obsolet.

Embedding wird in LIARA nicht mehr als Docker-Compose-Service betrieben. Der Embedding-Dienst laeuft immer als externer IP-/Host-Endpunkt, im lokalen Standardfall auf `127.0.0.1:8030`.

## Aktueller Betrieb

- Primaer: nativer C++ OpenVINO-Dienst `LiaraEmbeddingService.exe`
- Fallback/Wrapper: Python-Service `services.embedding.app`
- Docker Compose startet keinen `liara-embedding` Service.
- Containerisierte Dienste, insbesondere `liara-memory`, erreichen Embedding ueber `EMBEDDING_SERVICE_BASE_URL`.
- Lokal aus Docker heraus ist der erwartete Zielpunkt typischerweise `http://host.docker.internal:8030`.

## Konsequenz

Nicht mehr verwenden:

```powershell
docker compose build liara-embedding
docker compose up liara-embedding
docker compose logs liara-embedding
```

Stattdessen den externen Embedding-Prozess direkt starten und pruefen:

```powershell
src\emeddingserver\build-msvc-clean\LiaraEmbeddingService.exe --port=8030
curl http://127.0.0.1:8030/health
```

Python nur als Fallback/Wrapper:

```powershell
python -m uvicorn services.embedding.app:create_embedding_service_app --factory --host 127.0.0.1 --port 8030
```
