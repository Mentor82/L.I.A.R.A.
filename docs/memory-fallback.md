Ja, genau — das ist ein sinnvoller **Degraded-Mode**:

```text
Normalbetrieb:
API → Auth → UserContext → Memory-Service → Postgres/Redis/Qdrant/Neo4j

Fallback:
API → Auth → UserContext → Postgres-only
```

Wichtig dabei:

```text
Postgres-only darf Identität, Rollen, Sessions, Audit und harte Fakten liefern.
Postgres-only darf NICHT semantisches Memory, Ähnlichkeitssuche oder Graph-Kontext simulieren.
```

Saubere Regel für LIARA:

```markdown
## Degraded Auth/Memory Mode

Wenn der Memory-Service nicht healthy ist, wechselt LIARA in einen eingeschränkten Postgres-only Betrieb.

### Erlaubt im Fallback

- Login / Auth prüfen
- UserContext aus Postgres bauen
- Rollen und Permissions laden
- Session validieren
- Audit-Events schreiben
- einfache Chat-Nutzung ohne semantisches Langzeitgedächtnis

### Nicht erlaubt im Fallback

- semantische Erinnerung
- Vector Search
- Graph Relations
- automatische Kontextanreicherung
- personenbezogene Memory-Schlüsse
- Tool-Entscheidungen auf Basis fehlender Memory-Daten

### Verhalten

- Antwort bekommt Flag: `memory_degraded=true`
- Orchestrator erhält reduzierten Kontext
- UI zeigt optional: „Memory eingeschränkt“
- Tool-Aufrufe mit Memory-Abhängigkeit werden blockiert oder downgraded
```

Merksatz:

```text
Wenn Memory ausfällt, darf LIARA noch wissen, WER du bist —
aber nicht so tun, als wüsste sie alles, WAS sie über dich weiß.
```
