# Orchestrator Write-Intent Design Fix

**Problem**: Der Orchestrator erkannte Write-Intent (`"speichern"`, `"schreiben"` etc.) im Router (Stage 1), aber konnte die Anfrage nicht ausführen – nur Tips geben.

**Root Cause**: Stage 2 (`select_sys_command()`) hatte nur 3 hardcodierte Regex-Patterns für Write:

- `_WRITE_QUOTED_RE`: `write "CONTENT" to FILE`  
- `_EMPTY_FILE_RE`: `create empty file FILE`
- `_DIR_RE`: `create folder DIR`

Alle anderen Write-Requests fielen durch den Fallback zu Web-Lookup (Wikipedia), obwohl `needs_sys()` sie bereits erkannt hatte.

## Solution: LLM-Based Parameter Extraction

**Design**: Füge einen **LLM-basierten Fallback** zwischen explizite Pattern-Matches und dem Web-Lookup ein:

```text
select_sys_command(query)
  ├─ Explicit Patterns (WRITE_QUOTED_RE, EMPTY_FILE_RE, DIR_RE)
  │   └─ Falls Match → return konkrete SysCommandSelection
  │
  └─ Wenn kein Pattern matched, aber Write-Keywords vorhanden:
      └─ LLM-Extraktion (mit Inference-Service)
          ├─ Prompt: "Extract file write parameters"
          ├─ Response: JSON mit target_path, content, write_mode, storage_scope
          └─ Dispatch: mkdir / touch / tee basierend auf write_mode
      
      └─ Falls LLM-Extraktion fehlschlägt oder keine Invoker:
          └─ Fallback zu Web-Lookup (wie vorher)
```

## Files Changed

**New**:

- `services/orchestrator/write_intent_extractor.py` – LLM-basierte Parameter-Extraktion

**Modified**:

- `services/orchestrator/sys_selector.py`:
  - `select_sys_command()` erhält optional `inference_invoker` Parameter
  - Vor Web-Fallback: LLM-Extraktion versuchen

- `services/orchestrator/executor.py`:
  - `_build_sys_parameters()` ist nicht mehr @staticmethod
  - Ruft `ensure_inference_invoker()` auf um Inference-Service zu holen
  - Übergibt Invoker an `select_sys_command()`

- `tests/unit/test_orchestration_split.py`:
  - Fixed: `test_route_keyword_heuristic` – Intent kann `sys_time` oder `sys_datetime` sein

**New Tests**:

- `tests/unit/test_write_intent_extraction.py` – Umfassende Tests für:
  - Write-Intent Parameter-Extraktion
  - Managed Path-Auflösung
  - Explizite Patterns (Fallback-Validierung)
  - LLM-Mock-Tests für alle Write-Modi (overwrite, append, mkdir, touch)

## How It Works

***Example 1: Explicit Pattern (No LLM)***

```text
User: write "print('hello')" to test.py
→ Matches _WRITE_QUOTED_RE
→ Returns: tee test.py with stdin
```

***Example 2: Natural Language (LLM-Based)***

```text
User: speichere ein python script mit hello world
→ No explicit pattern match
→ has write keyword "speichere"
→ LLM extracts: {target_path: "script.py", content: "...", write_mode: "overwrite"}
→ Returns: tee /home/liara/workspace/script.py with stdin
```

***Example 3: Fallback (Web Lookup)***

```text
User: was ist python?
→ No write keywords
→ Falls through
→ Returns: Wikipedia search via curl
```

## Benefits

1. **Flexible Natural Language**: Users können sagen "speichre eine Datei mit X" statt nur "write X to FILE"
2. **Backward Compatible**: Explizite Patterns funktionieren weiterhin
3. **Graceful Degradation**: Kein Inference-Service? → Fallback funktioniert
4. **Managed Paths**: Alle Writes landen in `/home/liara/workspace/` oder `/home/liara/temp/`
5. **Type-Safe**: LLM gibt strukturiertes JSON, wird validiert

### Integration Point

Die LLM-Extraktion wird **optional** aktiviert:

- *Mit Inference-Service*: Full LLM support für Write-Intent
- *Ohne Inference-Service*: Nur explizite Patterns + Web-Fallback (wie vorher)

Kein Breaking Change!
