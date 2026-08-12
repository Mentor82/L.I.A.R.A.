# LLM Fundamentals — Wie funktioniert ein Sprachmodell?

> Erstellt: 2026-04-27  
> Kontext: LIARA-Session, Frage nach dem inneren Ablauf eines LLMs

---

## Die Kernfrage

> *"Ein LLM wandelt Text in Vektoren um, sucht Ähnlichkeiten, und gibt dann wahrscheinlichkeitsbasiert eine Antwort — und beim Fine-Tuning werden Fakten in die Gewichte eingebrannt?"*

Teilweise richtig. Hier die präzise Version.

---

## Der Ablauf in 5 Schritten

### 1. Tokenisierung
Der Eingabetext wird in **Tokens** zerlegt — das sind keine Wörter, sondern Subwort-Einheiten.

```
"Hauptstadt" → ["Haupt", "stadt"]
"Berlin"     → ["Berlin"]
```

Jedes Token hat eine fixe ID im Vokabular (z. B. 50.000–100.000 Einträge).

---

### 2. Embedding — Text wird zum Vektor
Jede Token-ID wird in einen hochdimensionalen Vektor umgewandelt (typisch: 768–4096 Dimensionen).

- Ähnliche Konzepte liegen im Vektorraum nah beieinander.
- Diese Embeddings sind **lernbare Parameter** — sie verändern sich beim Training.

> Hier ist deine Intuition korrekt: Text wird in Vektoren umgewandelt.

---

### 3. Attention — Kontext verstehen
Der Transformer-Kern. Jedes Token "schaut" auf alle anderen Tokens und berechnet, welche relevant sind.

```
Query × Key → Attention-Score → gewichtete Summe der Values
```

- Multi-Head Attention: Mehrere parallele Attention-Köpfe, jeder lernt andere Beziehungen.
- Ermöglicht: Koreferenzauflösung, Syntax, semantische Abhängigkeiten über lange Distanzen.

Das passiert in **N gestapelten Transformer-Blöcken** (z. B. 32 bei LLaMA-7B, 96 bei GPT-4).

---

### 4. Gewichte — Wo steckt das "Wissen"?
Nach dem Attention-Block kommt ein **Feed-Forward-Netzwerk (FFN)** pro Layer. Hier werden Fakten und Muster komprimiert gespeichert — aber nicht als strukturierte Datenbank, sondern als **statistische Korrelationen in Milliarden von Gewichtsmatrizen**.

> **Wichtige Korrektur zum Fine-Tuning:**  
> Beim Fine-Tuning werden Fakten **nicht** sauber "eingebrannt". Stattdessen werden die Gewichte so verschoben, dass das Modell neue Muster bevorzugt. Das führt zu:
> - **Catastrophic Forgetting** (alte Fakten können überschrieben werden)
> - **Halluzinationen** (das Modell interpoliert zwischen Trainingsdaten)
> - Kein garantierter Abruf — eher Wahrscheinlichkeitsverschiebung

Fine-Tuning ≠ Datenbankschreiben. Es ist eher wie: *"Verändere die Neigungen eines riesigen Neuronengeflechts leicht."*

---

### 5. Ausgabe — Wahrscheinlichkeitsverteilung
Am Ende jedes Forward-Pass entsteht ein **Logit-Vektor** über das gesamte Vokabular.

```
Softmax(Logits) → Wahrscheinlichkeit für jedes mögliche nächste Token
```

- Temperatur 0.0 → immer das wahrscheinlichste Token (deterministisch)
- Temperatur 1.0 → Sampling aus der Verteilung (kreativ/variabel)
- Top-p / Top-k → weitere Sampling-Filter

Das Modell **generiert Token für Token**, nicht den ganzen Satz auf einmal.

---

## Quantisierung — kurze Notiz

Standardmäßig: Gewichte als float32 (4 Byte pro Wert).  
Quantisierung reduziert auf int8 oder int4:

| Format | Bit | Speicher LLaMA-7B |
|--------|-----|-------------------|
| float32 | 32 | ~28 GB |
| float16 | 16 | ~14 GB |
| int8    | 8  | ~7 GB  |
| int4    | 4  | ~3.5 GB|

- Vorteil: passt auf Consumer-Hardware (RAM/VRAM)
- Nachteil: kleine Qualitätseinbußen, besonders bei präzisen Fakten

OpenVINO kann f16 und INT8 ausführen; im LIARA-Setup wird aktuell f16 für die Inferenz verwendet, sowohl für LLM- als auch für Embedding-Modelle.

---

## Zusammenfassung

```
Eingabe-Text
    ↓ Tokenizer
Token-IDs
    ↓ Embedding-Matrix
Vektoren (hochdimensional)
    ↓ N × [Multi-Head Attention + FFN]
Kontextualisierte Repräsentation
    ↓ LM-Head (lineare Projektion)
Logits über Vokabular
    ↓ Softmax + Sampling
Nächstes Token
    ↓ (wiederholen bis EOS)
Antwort
```

**Was ein LLM ist:** Ein statistischer Kompressor über riesige Textmengen — er hat gelernt, welche Token-Sequenzen wahrscheinlich aufeinander folgen.

**Was ein LLM nicht ist:** Eine Datenbank mit abrufbaren Fakten. Deshalb braucht LIARA RAG + explizite Fact-Stores (Redis/Postgres) — das Modell liefert Sprachkompetenz, die Infrastruktur liefert Fakten.

---

*Für LIARA-spezifische Architektur: siehe [ARCHITECTURE.md](ARCHITECTURE.md)*
