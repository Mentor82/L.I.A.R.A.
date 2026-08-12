# Embedding Service Contract — Scheduler ↔ LiaraEmbeddingService

> Gültig für: `LiaraHelperScheduler` (Port 8040) → `LiaraEmbeddingService` (LiNeP TCP Port **8767**, UDP **8768**)
>
> **Transport: LiNeP TCP — kein HTTP.**  
> Der EmbeddingService hört auf einem LiNeP-TCP-Port und kommuniziert über das Standard-LiNeP-Wire-Format (24-Byte-Header + JSON-Payload).

---

## Verbindungsübersicht

```
Scheduler                                    EmbeddingService
   |                                               |
   |-- TCP connect (port 8767) ------------------> |
   |-- LiNeP Header (24 Bytes, EMBED_REQUEST) ---> |
   |-- JSON Payload (input_text, normalize)  ----> |
   |<-- LiNeP Header (24 Bytes, EMBED_RESPONSE) -- |
   |<-- JSON Payload (item.vector, model, ...) ---- |
   |-- TCP close                                    |
```

Pro `/infer/helper`-Aufruf: **1 + N TCP-Verbindungen** (1x Prompt-Kontext, Nx Helper-Antworten).  
Jede Verbindung ist eine eigene kurzlebige TCP-Session (connect -> senden -> empfangen -> close).

---

## Ports & Konfiguration

| Feld                    | Default       | Beschreibung                              |
|-------------------------|---------------|-------------------------------------------|
| `linep_tcp_port`        | **8767**      | LiNeP TCP — Standard-Port (wie Helper)    |
| `linep_heartbeat_port`  | **8768**      | UDP Heartbeat — Standard-Port (wie Helper)|
| `linep_worker_id`       | **30**        | Worker-ID (fest, identifiziert Embedding) |
| `linep_slot_id`         | 0             | Slot-ID                                   |
| `linep_heartbeat_interval_ms` | 1000  | Heartbeat-Intervall ms                    |

---

## LiNeP Wire-Format

### Header (24 Bytes, packed)

| Byte-Offset | Feld             | Typ    | Wert / Bedeutung                                          |
|-------------|------------------|--------|-----------------------------------------------------------|
| 0–1         | `magic`          | uint16 | `0x4C4E` (ASCII "LN")                                    |
| 2           | `version`        | uint8  | `0x01`                                                    |
| 3           | `msg_type`       | uint8  | `0x30` = EMBED_REQUEST  /  `0x31` = EMBED_RESPONSE        |
| 4–5         | `header_len`     | uint16 | `24` (Basis, ohne Erweiterungen)                          |
| 6–7         | `flags`          | uint16 | Bitmask, s. Flags-Tabelle                                 |
| 8–11        | `payload_len`    | uint32 | Länge des JSON-Bodys in Bytes                             |
| 12–15       | `sequence`       | uint32 | Sender-lokaler Zähler                                     |
| 16–19       | `correlation_id` | uint32 | Request <-> Response matching                             |
| 20–21       | `worker_id`      | uint16 | Worker-ID des Senders                                     |
| 22          | `slot_id`        | uint8  | Slot-ID des Senders                                       |
| 23          | `header_crc`     | uint8  | CRC-8 über Bytes [0..22]                                  |

#### Relevante Flags (uint16, Bitmask)

| Bit | Name            | Hex      | Bedeutung                                   |
|-----|-----------------|----------|---------------------------------------------|
| 2   | `FLAG_ERROR`    | `0x0004` | Payload ist Fehler-JSON                     |
| 8   | `FLAG_DEGRADED` | `0x0100` | Service degradiert (CPU-Fallback o.ä.)      |

---

## MsgType-Werte (Embedding)

| Name                  | Hex    | Richtung                                 |
|-----------------------|--------|------------------------------------------|
| `EMBED_REQUEST`       | `0x30` | Scheduler -> EmbeddingService            |
| `EMBED_RESPONSE`      | `0x31` | EmbeddingService -> Scheduler            |
| `SIMILARITY_REQUEST`  | `0x32` | (reserviert, noch nicht genutzt)         |
| `SIMILARITY_RESPONSE` | `0x33` | (reserviert, noch nicht genutzt)         |
| `MSG_ERROR`           | `0x13` | EmbeddingService -> Scheduler (Fehler)   |

---

## Request-Payload (JSON, nach Header)

```json
{
  "input_text": "<string>",
  "normalize":  true
}
```

| Feld         | Typ     | Pflicht | Beschreibung                                         |
|--------------|---------|---------|------------------------------------------------------|
| `input_text` | string  | ja      | Rohtext, der embedded werden soll                    |
| `normalize`  | boolean | nein    | `true` -> L2-normiert (default laut EmbeddingConfig) |

---

## Response-Payload (JSON, nach Header) — EMBED_RESPONSE

```json
{
  "item": {
    "model":      "Qwen3-Embedding-0.6B-fp16-ov",
    "dimensions": 1024,
    "vector":     [0.012, -0.334, 0.871, "..."],
    "metadata": {
      "runtime":   "openvino-cpp",
      "transport": "linep"
    }
  }
}
```

| Feld                      | Typ            | Beschreibung                                       |
|---------------------------|----------------|----------------------------------------------------|
| `item.model`              | string         | Modell-Name / Pfad                                 |
| `item.dimensions`         | int            | Vektor-Dimension                                   |
| `item.vector`             | array<float>   | Embedding-Vektor (L2-normiert wenn normalize=true) |
| `item.metadata.runtime`   | string         | `"openvino-cpp"`                                   |
| `item.metadata.transport` | string         | `"linep"`                                          |

> Der Scheduler liest `item.vector` für das Scoring. `item.model`, `item.dimensions` und `item.metadata` stehen für Diagnose zur Verfügung.

---

## Error-Payload (JSON) — bei MSG_ERROR oder FLAG_DEGRADED

```json
{
  "status":    "failed",
  "error":     "unsupported_msg_type",
  "degraded":  true,
  "runtime":   "openvino-cpp",
  "slot_type": "embedding"
}
```

---

## Fehlerverhalten (Scheduler-seitig)

| Situation                          | Verhalten                                          |
|------------------------------------|----------------------------------------------------|
| TCP-Verbindung fehlgeschlagen      | Leerer Vektor `[]`                                 |
| `FLAG_ERROR` in Response-Header    | Leerer Vektor (Payload wird verworfen)             |
| JSON-Parsefehler                   | Leerer Vektor (Exception abgefangen)               |
| `item.vector` fehlt / kein Array   | Leerer Vektor                                      |
| Leerer Vektor im Scoring           | `cosine_similarity = 0.0`, `final_score = 0.0`     |
| `input_text` leer oder fehlt       | `MSG_ERROR` + `FLAG_ERROR` + `{"error": "empty_input_text"}` |

Graceful degradation — der Scheduler bricht bei Embedding-Fehlern **nicht** ab.

> **Timeouts (Scheduler-seitig, implementiert):** connect <= 500 ms
> (non-blocking + `select`), recv/send <= 300 ms
> (`SO_RCVTIMEO`/`SO_SNDTIMEO`).
> Bei Ueberlast muss der EmbeddingService sofort `MSG_ERROR` zurueckgeben.

---

## Scoring-Formel im Scheduler

Nachdem alle Vektoren gesammelt sind:

```
relevance(Ai)            = cosine_similarity(vec_context, vec_Ai)
pair_avg(Ai)             = mean( cosine_similarity(vec_Ai, vec_Aj) ) fuer alle j != i
context_gate(Ai)         = clamp01((relevance(Ai) - context_min_similarity) / (1 - context_min_similarity))
grounded_consensus(Ai)   = pair_avg(Ai) * context_gate(Ai)
disagreement_penalty(Ai) = max(0, pair_avg(Ai) - relevance(Ai)) * 0.35
final(Ai)                = clamp01(0.8 * relevance(Ai) + 0.2 * grounded_consensus(Ai) - disagreement_penalty(Ai))
```

Der Helper mit dem höchsten `final_score` gewinnt. Wichtig: Task-Grounding gegen den gemeinsamen Context wird hoeher gewichtet als reine Antwort-zu-Antwort-Aehnlichkeit.

## Consensus-Request (task-grounded)

`CONSENSUS_REQUEST` (`0x40`) wird als JSON-Body erwartet:

```json
{
  "task_type": "quick_extract",
  "source_text": "...aufgabenkontext...",
  "candidates": ["antwort A", "antwort B", "antwort C"],
  "threshold": 0.65,
  "context_weight": 0.8,
  "consensus_weight": 0.2,
  "context_min_similarity": 0.4
}
```

Alias: `answers` wird alternativ zu `candidates` akzeptiert.

---

## Implementierungsstand

| Komponente | Status | Hinweis |
|---|---|---|
| `embed_text_linep()` im Scheduler | ✅ | LiNeP TCP, `EMBED_REQUEST` 0x30 / `EMBED_RESPONSE` 0x31 |
| `--embedding-linep=host:port` CLI-Arg | ✅ | Default `127.0.0.1:8767` |
| `--embedding-http=` Legacy-Alias | ✅ | Wird akzeptiert, wirkt identisch |
| EmbeddingService `LinepEmbeddingEndpoint` | ✅ | TCP 8767, UDP Heartbeat 8768 |
| `embedding_config.example.toml` | ✅ | `enabled=true`, Ports 8767/8768 |
| `FLAG_DEGRADED` im Scheduler ausgewertet | ✅ | Vektor wird akzeptiert, Warning geloggt |

---

## Anforderungen an den EmbeddingService

| # | Anforderung                                                                                  |
|---|----------------------------------------------------------------------------------------------|
| 1 | LiNeP TCP auf Port **8767** (konfigurierbar via `linep_tcp_port`)                           |
| 2 | UDP Heartbeat auf Port **8768**, Worker-ID 30, Slot-ID 0                                    |
| 3 | Akzeptiert `EMBED_REQUEST` (0x30), antwortet mit `EMBED_RESPONSE` (0x31)                    |
| 4 | Bei unbekanntem `msg_type`: antwortet mit `MSG_ERROR` + `FLAG_ERROR`                        |
| 5 | `item.vector` muss ein JSON-Array aus `float` sein                                          |
| 6 | Bei `"normalize": true` -> L2-normierter Vektor (||v|| = 1.0)                               |
| 7 | Vektor-Dimension muss für alle Calls in einer Session identisch sein                        |
| 8 | Antwortzeit <= 200 ms empfohlen; Scheduler hat connect/read/write Timeouts und degradiert auf leeren Vektor |
