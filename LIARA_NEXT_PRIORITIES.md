# LIARA — Next Priorities

Stand: 2026-08-11

Diese Liste verdichtet die offenen Punkte aus `docs/00_index.md`. Sie definiert
pruefbare Akzeptanz statt nur Ziele.

## Erledigte P0 & P1-Meilensteine (Stand 2026-08-11)

- [x] **NPU-Embedding-Pfad:** Als verifiziert und funktionstüchtig eingestuft.
- [x] **ai-validator `quick`-Scope:** Scoping, Timeouts und Include/Exclude-Regeln als verifiziert eingestuft.
- [x] **Validator-Findings strukturieren:** `ValidatorFinding` 5-Tupel `{file_path, line, rule, severity, message}` implementiert, geparst und persisiert.
- [x] **Scout-Vektoreinbindung (`SCOUT_USE_REAL_EMBEDDINGS`):** Real 1024-D OpenVINO Vector Routing (:8030), Redis Caching, versionierte `IntentProfile`-Objekte, Fallback-Parität und transparente `RouterDecision.metadata` implementiert und getestet.
- [x] **Workspace-Agent E2E-Abnahme & WSL/Docker-Monitoring:** WSL2 Debian Distro, 5 Docker-Container (`liara-neo4j`, `liara-qdrant`, `liara-chroma`, `liara-redis`, `liara-postgres`) healthy. Subprocess-Cleanups (`proc.kill()`) verifiziert. 53 Unit-Tests grün (27,7s).
- [x] **Provider- und Dependency-Konfiguration vereinheitlichen (P1):** `services/config/settings.py` konsolidiert (`DEFAULT_LLM_TIMEOUT_SECONDS`, vereinheitlichte Store/Service-URLs, strukturierte Sektionen, `Settings.to_dict()` Export).
- [x] **Unit-Test Baseline:** 100 % grün (1384 passed, 0 failed, 5 skipped opt-in).

## P2 — App-Server (`services/api/app.py`) modularisieren

Ziel: Die monolithische `app.py` in kleine FastAPI-Router aufteilen.

Akzeptanz:

- `app.py` kapselt nur noch App-Creation und Middleware.
- Endpunkte liegen in lesbaren Submodulen (`routers/chat.py`, `routers/tools.py`, etc.).
- 100% der API-Unit-Tests bleiben ohne Verhaltensänderung grün.
