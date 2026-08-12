# Build History Tracker

`scripts/build_history.py` ist der lokale Aenderungs- und Build-Tracker fuer das LIARA-Projekt.
Er speichert Eintraege in `.build_history.sqlite` im Projekt-Root und bietet aktuell diese Arbeitsweisen:

- CLI-Tabelle (`list`)
- Suche (`search`)
- JSON-Export (`export`)
- Bearbeiten/Loeschen (`update`, `delete`)
- interaktive TUI (`tui`)
- generische Eintraege (`add`) und rueckwaertskompatibles `record`

**Befehle im Ueberblick:** `record` · `add` · `list` · `search` · `update` · `delete` · `export` · `tui`

---

## Datenbankschema

| Feld | Typ | Beschreibung |
| ---- | --- | ------------ |
| `id` | INTEGER | Automatisch vergebene laufende ID |
| `timestamp` | DATETIME | Zeitpunkt des Eintrags (UTC, automatisch gesetzt) |
| `category` | TEXT | Eintragstyp: `Idee`, `Geplant`, `In Arbeit`, `Umgesetzt`, `Fix`, `Test`, `Verworfen` |
| `version` | TEXT | Versionsstring, z. B. `0.1.1` |
| `component` | TEXT | Betroffene Komponente, z. B. `orchestrator`, `api` |
| `os` | TEXT | Betriebssystem (wird automatisch ermittelt) |
| `status` | TEXT | `success`, `failed` oder `skipped` |
| `duration` | REAL | Laufzeit in Sekunden (optional, fuer CI-Laeufe) |
| `worker` | TEXT | Wer die Arbeit durchgefuehrt hat, z. B. `GitHub Copilot`, `ci`, `human` |
| `notes` | TEXT | Freitext: Idee, Aenderungsbeschreibung, Commit-Hash o. A. |
| `entry_type` | TEXT | Art des Eintrags: `build`, `idea`, `chat`, `decision`, `fix`, `test`, `note` |
| `source` | TEXT | Quelle des Eintrags, z. B. `human`, `script`, `ci`, `chat` |
| `project` | TEXT | Projektname, z. B. `liara` |
| `topic` | TEXT | Fachthema/Schwerpunkt, z. B. `history`, `policy`, `routing` |
| `title` | TEXT | Kurzer Titel fuer generische Eintraege |
| `tags` | TEXT | Tags als kommagetrennte Liste |
| `meta_json` | TEXT | JSON-Metadaten als String (`{}` als Default) |

Die Datenbank und alle Spalten werden beim ersten Start automatisch angelegt. Bestehende Datenbanken werden per `ALTER TABLE` migriert — kein manuelles Schema-Update nötig.

### Defaults und Mapping

- `record` bleibt rückwärtskompatibel und setzt intern immer `entry_type=build`.
- `add --content` wird im bestehenden Feld `notes` gespeichert.
- `add --title` wird im Feld `title` gespeichert.
- `meta_json` wird beim Schreiben validiert und beim `export` nach Möglichkeit als echtes JSON ausgegeben.

---

## Befehle

### Eintrag schreiben

```bash
python scripts/build_history.py record \
  --version   "0.1.1" \
  --component "orchestrator" \
  --status    "success" \
  --duration  12.5 \
  --worker    "GitHub Copilot" \
  --category  "Umgesetzt" \
  --notes     "Kurze Beschreibung der Änderung"
```

| Argument | Pflicht | Beschreibung |
| -------- | ------- | ------------ |
| `--version` | ✅ | Versionsstring |
| `--component` | ✅ | Name der Komponente |
| `--status` | ✅ | `success` / `failed` / `skipped` |
| `--category` | – | `Idee` / `Geplant` / `In Arbeit` / `Umgesetzt` / `Fix` / `Test` / `Verworfen` (Standard: leer) |
| `--duration` | – | Laufzeit in Sekunden (Standard: `0`) |
| `--worker` | – | Wer die Arbeit gemacht hat (Standard: leer) |
| `--notes` | – | Freitext-Notiz (Standard: leer) |

---

### Verlauf anzeigen

```bash
python scripts/build_history.py list
python scripts/build_history.py list --limit 20
python scripts/build_history.py list --category Idee
python scripts/build_history.py list --category Umgesetzt --component orchestrator
```

Gibt eine formatierte CLI-Tabelle aus. Standard-Limit: 50 Einträge.  
`--category` und `--component` können einzeln oder kombiniert als Filter genutzt werden.

---

### JSON-Export

```bash
python scripts/build_history.py export
python scripts/build_history.py export > build_history.json
```

Exportiert bis zu 1000 Einträge als JSON-Array — geeignet für automatisierte Auswertung oder AI-Ingestion.

---

### Eintrag aktualisieren

```bash
# Status ändern:
python scripts/build_history.py update 22 --status success

# Notes ersetzen:
python scripts/build_history.py update 22 --notes "Neuer Beschreibungstext"

# Bemerkung anhängen (bestehende Notes bleiben erhalten):
python scripts/build_history.py update 22 --append-notes "Nachträgliche Korrektur: Timeout-Handling gefixt"

# Mehrere Felder auf einmal:
python scripts/build_history.py update 22 --status success --category Umgesetzt --worker copilot
```

| Argument | Beschreibung |
| -------- | ------------ |
| `id` | ID des zu aendernden Eintrags (Pflichtargument) |
| `--status` | Neuer Status: `success` / `failed` / `skipped` |
| `--notes` | Notes-Feld **ersetzen** |
| `--append-notes` | Text an bestehende Notes **anhaengen** (mit Leerzeile als Trenner) |
| `--category` | Neue Kategorie |
| `--worker` | Neuer Worker |
| `--component` | Neue Komponente |
| `--version` | Neue Version |
| `--title` | Neuer Titel |
| `--topic` | Neues Topic |
| `--project` | Neues Projekt |
| `--source` | Neue Source |
| `--tags` | Neue Tags |

---

### TUI (Textual User Interface)

```bash
python scripts/build_history.py tui
```

Startet eine interaktive Terminal-UI (zebra-Tabelle, per `r` aktualisierbar, per `q` beenden).  
Benötigt das optionale Package `textual`:

```bash
pip install textual
```

TUI-Interaktion:

- `v`: Detailansicht (Overlay) fuer den aktuell markierten Eintrag
- Klick auf einen Eintrag: Detailansicht (Overlay)
- `a`: Auto-Refresh ein-/ausschalten
- `r`: manuelles Refresh
- `n`: neuer Eintrag
- `e`: Eintrag bearbeiten
- `d`: Eintrag loeschen
- `q`: TUI beenden

---

## Typische Workflows

### Eine Idee festhalten (Mensch)

```bash
python scripts/build_history.py record \
  --version "0.1.1" --component "planner" \
  --status skipped --worker "human" --category "Idee" \
  --notes "ASCII-Umlaute per regex statt Stopwort-Schwelle"
```

### Nach einem manuellen Fix-Zyklus

```bash
python scripts/build_history.py record \
  --version "0.1.0" --component "planner" \
  --status success --worker "human" --category "Umgesetzt" \
  --notes "Stopword-Threshold angepasst"
```

### Nach einem CI-Lauf

```bash
python scripts/build_history.py record \
  --version "$BUILD_VERSION" --component "api" \
  --status "$CI_STATUS" --duration "$CI_ELAPSED" \
  --worker "ci" --notes "$GIT_COMMIT"
```

### Nach einer Copilot-Session

```bash
python scripts/build_history.py add \
  --type note --category Umgesetzt --status success \
  --component orchestrator --worker "GitHub Copilot" \
  --project liara --topic routing \
  --title "Routing und Judge erweitert" \
  --content "Reward-Routing, Judge-Integration und Lazy-Imports umgesetzt"
```

### Doku-Bereinigung dokumentieren

```bash
python scripts/build_history.py add \
  --type note --category Entscheidung --status success \
  --component docs --worker "GitHub Copilot" \
  --project liara --topic documentation \
  --title "Statusdoku konsolidiert" \
  --content "Ist-/Soll-Stand bereinigt, veraltete Completion-Reports entfernt"
```

---

## Datenbankpfad

```text
<projekt-root>/.build_history.sqlite
```

Die Datei liegt im Root und wird nicht eingecheckt (empfohlen: `.gitignore`-Eintrag ergänzen):

```text
.build_history.sqlite
```
