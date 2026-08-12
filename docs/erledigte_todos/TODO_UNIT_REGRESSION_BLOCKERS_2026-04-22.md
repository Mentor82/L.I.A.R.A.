# TODO Unit Regression Blockers (2026-04-22)

Status: erledigt  
Kontext: Nach Scout-Embedding-Integration war der breite Lauf pytest tests/unit -q teilweise rot; alle identifizierten Blocker wurden bereinigt.

## Erledigt

- [x] Continue-Bridge Mock-Signaturen angepasst (tests/unit/test_continue_openai_bridge.py)
- [x] Embedding Chat Flow Erwartungswerte auf Two-Level-Ingestion angepasst (tests/unit/test_embedding_chat_flow.py)
- [x] Time Command Selection Erwartung aktualisiert und UTC-Time-Intent ergänzt (tests/unit/test_time_command_selection.py, services/orchestrator/sys_selector.py)
- [x] Retry-Flow-Erwartung auf aktuelle Retry-Control-Policy abgestimmt (tests/unit/test_orchestrator_retry_flow.py)
- [x] Policy-DB-Test auf aktuelles Layout db/<command>/(w|g|b).db angepasst (tests/unit/test_policy_db.py)
- [x] Import-Policy robust gegen Nicht-UTF8-Dateien gemacht (tests/unit/test_import_policy.py)
- [x] WSL-Executor-Test auf aktuelle Resolver-Logik angepasst (tests/unit/test_wsl_executor.py)

## Finale Verifikation

- [x] pytest tests/unit -q -> 887 passed, 5 skipped

## Offener Follow-up (nicht-blockierend)

- [x] RuntimeWarning in tests/unit/test_wsl_executor.py durch gemockte Subprocess-Coroutine aufgeraeumt (Test-Hygiene)

## Schnellstart fuer Follow-up

```powershell
pytest tests/unit/test_wsl_executor.py -q -W error::RuntimeWarning
```

Verifiziert: 39 passed (keine RuntimeWarning mehr)
