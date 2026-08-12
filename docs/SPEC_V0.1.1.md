# 🧠 Liara 0.1.1 – MCP / Spezialisierungsschicht

**Status:** BLOCKED

> Diese Erweiterung ist konzeptionell definiert, aber noch nicht aktiv.  
> Keine produktive Implementierung in 0.1.0 enthalten.

---

## 🎯 Ziel

Diese Spezifikation erweitert **Liara 0.1.0** um eine kontrollierte **Spezialisierungs- und Fachzugangsschicht (MCP / Domain Layer)**.

### Ziele

- domänenspezifische Tools integrieren
- Fachprofile bereitstellen
- Dev / Stage / Prod sauber trennen
- Erweiterbarkeit ohne Änderung des Grundcodes vorbereiten

### Wichtig

- Der **Orchestrator** entscheidet den Use Case
- Der **Memory-Service** entscheidet die Datenbank
- Der **MCP-/Domain-Layer** stellt Fachzugänge bereit
- **Kein direkter DB-Zugriff außerhalb des Memory-Service**

---

## 🧩 Grundprinzip

| Ebene | Aufgabe | Bedeutung |
|---|---|---|
| Orchestrator | Use Case entscheiden | Routing |
| Memory-Service | internes Wissen verwalten | Wahrheit, Retrieval, Kontext |
| MCP-/Domain-Layer | Fachzugänge bereitstellen | Tools, Spezialisierung |
| Datenbanken | Implementierungsdetails | Persistenz, Suche, Beziehungen |

---

## ⚙️ Regeln (STRICT)

### Regel 1
**Kein Service greift direkt auf Datenbanken zu.**

### Regel 2
**Postgres ist die einzige Quelle für persistente Wahrheit.**

### Regel 3
**Qdrant/Chroma wird nur für semantische Suche verwendet.**

### Regel 4
**Redis speichert keine Wahrheit.**

### Regel 5
**Neo4j wird nur für Beziehungen verwendet.**

### Regel 6
**MCP ist keine Quelle für persistente Wahrheit.**

### Regel 7
**MCP greift nicht direkt auf Datenbanken zu.**

Erlaubt:

`MCP -> Service -> Memory-Service -> DB`

Verboten:

`MCP -> DB direkt`

### Regel 8
**Der Orchestrator entscheidet, ob eine Anfrage über Memory, MCP oder Hybrid läuft.**

### Regel 9
**MCP erweitert fachlich, ersetzt aber keine Kernlogik.**

---

## 🧠 Merksatz

    Postgres = Wahrheit
    Qdrant = Erinnerung
    Redis = Jetzt
    Neo4j = Verständnis
    MCP = Fachwelt

---

## 🔄 Architektur

### Liara 0.1.0

`User -> Orchestrator -> Memory-Service -> MemoryAdapter -> DB`

### Liara 0.1.1

```text
User Request
   |
   v
Orchestrator
   |-----------------------> Memory-Service
   |                           |
   |                           v
   |                        MemoryAdapter
   |                           |
   |                           +--> Postgres
   |                           +--> Redis
   |                           +--> Qdrant/Chroma
   |                           +--> Neo4j
   |
   |-----------------------> MCP-/Domain-Layer
                               |
                               +--> Domain Profiles
                               +--> Approved Tools
                               +--> Specialized Adapters
                               +--> Internal Service APIs
```

---

## 🧩 Aufgabe des MCP-/Domain-Layers

Der MCP-/Domain-Layer liefert:

- Fachtools
- Spezialisierung
- Adapter
- Profile

Der MCP-/Domain-Layer liefert **nicht**:

- Wahrheit
- Persistenz
- Datenbanklogik
- zweite Wissensquelle

---

## 🧭 Routing

```python
if query_type == "fact":
    memory.load()

elif query_type == "semantic":
    memory.query()

elif query_type == "context":
    memory.build_context()

elif query_type == "specialized":
    mcp.execute(profile)

elif query_type == "hybrid":
    combine(memory, mcp)
```

---

## 🧰 Domain Profiles

### Beispiele

- `general`
- `programming`
- `electrical`
- `cad`
- `infrastructure`
- `documentation`

---

## 🏷️ Tool-Metadaten

```json
{
  "tool_id": "example",
  "domain": "programming",
  "env": "dev",
  "maturity": "validated",
  "capability": ["read", "write"],
  "risk": "medium",
  "enabled": true
}
```

---

## 📦 Capability-Modell

- `read`
- `analyze`
- `propose`
- `write`
- `validate`
- `publish`

---

## 🧪 Maturity-Modell

- `draft`
- `validated`
- `approved`
- `deprecated`

### Regel

**Prod lädt standardmäßig nur `approved` Tools.**

---

## 🌍 Environment-Modell

- `dev`
- `stage`
- `prod`

---

## 🔐 Risk-Modell

- `low`
- `medium`
- `high`

### Regel

**High-Risk-Tools dürfen in prod nur mit expliziter Freigabe oder separater Policy aktiviert werden.**

---

## 🔄 Hybrid-Prinzip

```python
context = memory.build_context(query)
tools = mcp.load_profile(domain_profile)

return combine(context, tools)
```

---

## 🚫 VERBOTEN

- direkter DB-Zugriff aus MCP-Tools
- zweite Wahrheitsquelle außerhalb Postgres
- Speicherung von Wahrheit in Redis
- Vermischung von Embeddings und strukturierten Daten
- Schatten-Datenhaltung in Fachtools
- ungefiltertes Laden aller Tools in prod
- Auto-Publish experimenteller Tools nach prod

---

## 🧪 Entwicklungsstrategie

### Phase 1
- Architektur definieren
- Profile definieren
- Tool-Metadatenmodell festlegen

### Phase 2
- erstes Tool anbinden
- Routing für Memory / MCP / Hybrid ergänzen

### Phase 3
- Governance
- Reifegrade
- Risk-Filter
- Dev / Stage / Prod sauber trennen

---

## ✅ Definition of Done

- Memory-Architektur bleibt unverändert gültig
- MCP als eigene Schicht ist definiert
- keine direkten DB-Zugriffe aus MCP
- mindestens ein Domain Profile ist definiert
- Tool-Metadatenmodell ist vorhanden
- Routing zwischen Memory / MCP / Hybrid ist möglich
- prod lädt nur freigegebene Tools
- ein erster PoC-Adapter funktioniert

---

## 🔥 Leitprinzip

    Memory = Wissen
    MCP = Fachzugang
    Orchestrator = Entscheidung