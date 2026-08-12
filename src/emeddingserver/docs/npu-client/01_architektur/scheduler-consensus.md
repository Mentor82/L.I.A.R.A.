# Scheduler And Consensus

## Scheduler Ablauf

```text
Task kommt rein
 → passende Slots filtern
 → harte Filter anwenden
 → Score berechnen
 → besten Slot waehlen
 → Task senden
```

## Triple-Inference Consensus Gate

```text
Task
 → Helper A
 → Helper B
 → Helper C

Ergebnisse
 → Embedding
 → Similarity

wenn >=2 Ergebnisse >= 0.99
 → akzeptieren
sonst
 → reject / retry
```

## Fast-Fail

```text
2 Ergebnisse vorhanden
Similarity >= 0.99
→ sofort akzeptieren
→ drittes Ergebnis optional ignorieren
```

## Konsens-Level

```text
2/3 = akzeptiert
3/3 = high confidence
<2 = fail
```
