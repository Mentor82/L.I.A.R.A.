# Security Principles

## Rollenabgrenzung

```text
Helper:
  - keine eigene Logik
  - keine DB-Zugriffe
  - keine Entscheidungen

Scheduler:
  - einzige Entscheidungsinstanz

Liara:
  - einzige Interpretationsinstanz
```

## Sicherheitsziele

- klare Verantwortungsgrenzen pro Komponente
- keine Entscheidungslogik in Compute-Workern
- minimierte Angriffsoberflaeche durch stateless Helper

## Operational Guardrails

- Device-Modus explizit setzen (`--device=npu` oder `--device=cpu`)
- keine Auto-Auswahl im Probe/Runtime-Gate
- Worker-Start nur nach erfolgreichem Runtime-Check
