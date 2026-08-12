# 📚 Liara Context Compression Strategy

> Version: v1.0  
> Erstellt: 2026-04-27  
> Abgrenzung: Dieses Dokument beschreibt **nur** die Komprimierung langer Gesprächshistorien.  
> Für Per-Step-Budget und adaptive β → siehe [Context_Control_Strategy.md](Context_Control_Strategy.md)

---

## Motivation

Lange Konversationen wachsen unkontrolliert. Wenn alle vorherigen Turns unverändert in den
nächsten Prompt fließen, entsteht dasselbe Problem wie bei einem langen Copilot-Chat:

- Token-Budget erschöpft sich
- Ältere, weniger relevante Turns verdrängen aktuelle
- Multi-Step Reasoning verliert den Faden

**Lösung:** Alte Turns periodisch zu verlustfreien Summary-Blöcken zusammenfassen —
die jüngsten Turns bleiben verbatim erhalten.

---

## Konzeptuelle Abgrenzung

| | Context Control | Context Compression |
| --- | --- | --- |
| **Was** | Budget steuern, Prioritäten setzen | Gesprächshistorie squashen |
| **Wann** | Jeder Reasoning-Step ab Step 1 | Periodisch, wenn History wächst |
| **Trigger** | Immer | `turn_count ≥ threshold` |
| **Code** | `context_controller.py` | `context_compressor.py` |
| **Analogie** | Ampel / Verkehrsregelung | Autobahnauffahrt / Merge |

---

## Ablauf

```text
Volle History (N Turns)
    ↓ should_compress(threshold=20)?
    ├── Nein → unverändert weiterleiten
    └── Ja  ↓
         Aufteilen: older_turns | recent_turns (letzte 6 verbatim)
              ↓
         older_turns in Fenster à 10 Turns
              ↓
         Jedes Fenster → [summary] Block (extraktiv oder LLM-gestützt)
              ↓
         [compressed_history]
         [summary] Block 1
         [summary] Block 2
         ...
         Recent Turn 1
         Recent Turn 2
         ...
         → compressed_history → weiter an Context Control
```

---

## Parameter

| Parameter | Standard | Beschreibung |
| ----------- | --------- | ------------- |
| `window_size` | 10 | Turns pro Summary-Fenster |
| `keep_recent_turns` | 6 | Letzte N Turns verbatim behalten |
| `max_summary_tokens` | 180 | Max Tokens pro Summary-Block |
| `threshold_turns` | 20 | Ab wann Kompression ausgelöst wird |

---

## Compression Output Schema

```json
{
  "compressed_history": "[compressed_history]\n[summary] ...\nTurn A\nTurn B",
  "original_turn_count": 32,
  "retained_turn_count": 9,
  "token_estimate": 480,
  "dropped_turns": 22,
  "summary_blocks": ["[summary] ..."],
  "metadata": {
    "source": "context_compressor",
    "action": "windowed_summary",
    "original_turns": 32,
    "summary_blocks": 3,
    "recent_turns_kept": 6,
    "dropped_turns": 22
  }
}
```

---

## Summary-Qualität

Aktuell: **extraktive** Zusammenfassung (erster Satz pro Turn, gekürzt).

Upgrade-Pfad: LLM-gestützte **abstrakte** Zusammenfassung via internem Modellaufruf.  
Die `_summarize_window()`-Methode in `context_compressor.py` ist dafür der einzige Touchpoint.

---

## Was darf eine Compression nicht verlieren

```text
- Explizit genannte Fakten (Namen, Zahlen, Entscheidungen)
- Offene Fragen aus älteren Turns
- Schlussfolgerungen aus vorherigen Reasoning-Schritten
- Session-State-Änderungen (z. B. Themenwechsel)
```

---

## Leitprinzip

```text
Keine Kompression ohne Summary.
Summary ersetzt, nicht ergänzt.
Jüngste Turns bleiben immer verbatim.
```
