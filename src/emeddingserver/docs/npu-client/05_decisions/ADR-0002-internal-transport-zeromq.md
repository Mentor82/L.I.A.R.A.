# ADR-0002: Interne Transport-Schicht — ZeroMQ

## Status

Accepted

## Context

Das System besteht aus einem Scheduler, bis zu N Helper-Prozessen (Inference-Worker),
einem Embedding-Service (Similarity-Gate) und optional weiteren spezialisierten Workern.

Die Anforderungen an den internen Transport:

- Bis zu 30+ Helper registrieren sich dynamisch beim Scheduler.
- Jeder Helper sendet regelmäßige Heartbeats mit seinem aktuellen Zustand.
- Der Scheduler verteilt Tasks an ausgewählte Helper (z.B. 3 für Consensus Gate).
- Antworten müssen dem ursprünglichen Request zugeordnet werden (Correlation ID).
- Der Scheduler muss ausgefallene Helper erkennen und aus dem Pool entfernen.
- Externe Clients (Liara/Orchestrator) sprechen HTTP.

Bisheriger Ansatz: HTTP/JSON direkt zwischen allen Komponenten.

Probleme bei HTTP für intern:

- Kein natives Worker-Pool-Pattern.
- Heartbeat über HTTP ist polling-basiert und schwerfällig.
- Port-Management bei vielen Workern fehleranfällig.
- Kein einheitliches Frame-Format für Correlation IDs.

## Decision

Externe API (Liara → Scheduler): bleibt **HTTP/JSON** — debugbar, tooling vorhanden.

Interne Kommunikation (Scheduler ↔ Helper ↔ Embedding): **ZeroMQ**.

### Socket-Patterns

```text
Helper → Scheduler   Heartbeat:   PUB (Helper) / SUB (Scheduler)
                     Port: 5556

Scheduler → Helper   Task-Dispatch: ROUTER (Scheduler) / DEALER (Helper)
                     Port: 5557

Scheduler → Embedding   Similarity:  REQ (Scheduler) / REP (Embedding)
                         Port: 5558
```

### Nachrichtenformat

Alle ZeroMQ-Frames: JSON UTF-8, kein Binary-Protokoll.

```json
// Heartbeat (Helper → Scheduler, PUB)
{
  "type": "HEARTBEAT",
  "helper_id": "h-001",
  "capabilities": ["instruct", "coder"],
  "device": "NPU",
  "warm": ["instruct"],
  "last_infer_ms": 340,
  "queue_depth": 0
}

// HELLO bei erstem Verbindungsaufbau
{
  "type": "HELLO",
  "helper_id": "h-001",
  "capabilities": ["instruct", "coder"],
  "device": "NPU"
}

// Task-Request (Scheduler → Helper, ROUTER/DEALER)
{
  "type": "TASK",
  "task_id": "t-uuid",
  "attempt_id": 1,
  "prompt": "...",
  "max_tokens": 200,
  "timeout_ms": 5000
}

// Task-Response (Helper → Scheduler)
{
  "type": "RESULT",
  "task_id": "t-uuid",
  "helper_id": "h-001",
  "output": "...",
  "infer_ms": 340,
  "status": "ok"
}
```

### Stale-Erkennung

Scheduler markiert Helper als `stale` wenn kein Heartbeat innerhalb `3 × heartbeat_interval_ms`.
Stale Helper erhalten keine neuen Tasks. Pending Tasks werden neu geroutet.

Standardwerte:
- `heartbeat_interval_ms`: 5000
- `stale_threshold_ms`: 15000

### Consensus Gate

Für Tasks die Consensus erfordern:
1. Scheduler wählt 3 Helper mit passender Capability aus dem READY-Pool.
2. Sendet denselben Task-Frame an alle 3 gleichzeitig (DEALER non-blocking).
3. Wartet auf erste 2 Antworten (Fast-Fail bei `similarity >= 0.99`).
4. Dritte Antwort wird ignoriert oder als Tiebreaker genutzt.

## Consequences

**Positiv:**
- Kein Port-Management pro Helper — alle verbinden sich zum Scheduler.
- Worker-Pool ist automatisch self-registering via HELLO-Frame.
- Heartbeat ist push-basiert, kein Polling.
- DEALER/ROUTER erlaubt parallelen Dispatch an N Helper.
- Keine externe Broker-Infrastruktur nötig.

**Negativ / Akzeptiert:**
- Kein persistentes Message-Queueing (kein RabbitMQ/Kafka-Niveau).
- Retry/Ack/Timeout muss in Scheduler-Logik implementiert werden.
- ZeroMQ muss als Dependency gevendert werden (`vendor/zeromq/`).
- Windows-Build erfordert `libzmq.lib` + `zmq.hpp` (cppzmq header-only).

## Alternativen verworfen

| Option | Grund |
|---|---|
| HTTP/JSON intern | Overhead, kein Worker-Pool-Pattern, Heartbeat umständlich |
| Named Pipes | Nicht remote-fähig, kein N:M-Pattern |
| gRPC | Zu hoher Buildaufwand (protobuf codegen), overkill für 2 Services |
| NATS | Externer Broker-Prozess nötig, zusätzliche Betriebskomplexität |
