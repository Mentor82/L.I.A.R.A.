````md
# LiNeP v1.0 Draft
## Liara Neural Protocol

Status: Draft  
Zielplattform: Windows / C++  
Transport: UDP + TCP  
Einsatz: Liara Scheduler ↔ Helper ↔ Embedding-Service

---

# 1. Ziel

LiNeP ist ein leichtgewichtiges Protokoll für verteilte AI-Inference-Helper.

Es dient für:

- Worker-Registrierung
- Heartbeat
- Slot-Status
- Task-Versand
- Result-Rückgabe
- Triple-Consensus
- Embedding-/Similarity-Pfade

Leitsatz:

```text
Helper erzeugen.
Embedding misst.
Scheduler bewertet.
Liara entscheidet.
````

---

# 2. Grundprinzip

```text
UDP = Zustand
TCP = Auftrag
```

UDP wird genutzt für:

```text
Heartbeat
Status
Register
Bye
```

TCP wird genutzt für:

```text
Task
Result
Error
Embedding
Consensus
```

---

# 3. Rollen

```text
Orchestrator
  stellt Aufgaben

Scheduler
  verwaltet Slots
  verteilt Tasks
  prüft Konsens

Helper
  führt Inference aus

Embedding-Service
  erzeugt Embeddings
  berechnet Similarity

Primary Liara
  verarbeitet final
```

---

# 4. Node- und Slot-Modell

Ein Worker kann mehrere Slots haben.

```text
Worker 17
  Slot 0 = instruct
  Slot 1 = coder
```

Ein Slot ist die eigentliche Ressource.

```text
Node ≠ Modell
Slot = Modell-Endpunkt
```

---

# 5. LiNeP Common Header

Jede LiNeP-Nachricht beginnt mit einem festen Header.

## 5.1 Header v1

```text
Offset  Size  Field
0       2     Magic
2       1     Version
3       1     MessageType
4       2     HeaderLength
6       2     Flags
8       4     PayloadLength
12      4     Sequence
16      4     CorrelationID
20      2     WorkerID
22      1     SlotID
23      1     HeaderCRC
```

Gesamtgröße:

```text
24 Byte
```

---

# 6. Magic

```text
Magic = 0x4C4E
ASCII = "LN"
```

LN steht für:

```text
LiNeP
Liara Neural Protocol
```

---

# 7. Version

```text
0x01 = LiNeP v1
```

---

# 8. Message Types

```text
0x01 HEARTBEAT
0x02 REGISTER
0x03 REGISTER_ACK
0x04 BYE

0x10 TASK
0x11 TASK_ACK
0x12 RESULT
0x13 ERROR

0x20 STATUS_REQUEST
0x21 STATUS_RESPONSE

0x30 EMBED_REQUEST
0x31 EMBED_RESPONSE
0x32 SIMILARITY_REQUEST
0x33 SIMILARITY_RESPONSE

0x40 CONSENSUS_REQUEST
0x41 CONSENSUS_RESPONSE

0xF0 PING
0xF1 PONG
```

---

# 9. Flags

16-bit Flagfeld.

```text
Bit 0  ACK_REQUIRED
Bit 1  IS_ACK
Bit 2  ERROR
Bit 3  COMPRESSED
Bit 4  ENCRYPTED
Bit 5  FRAGMENTED
Bit 6  FINAL_FRAGMENT
Bit 7  PRIORITY
Bit 8  DEGRADED
Bit 9  RETRY
Bit 10 RESERVED
Bit 11 RESERVED
Bit 12 RESERVED
Bit 13 RESERVED
Bit 14 RESERVED
Bit 15 RESERVED
```

Für v1 gilt:

```text
COMPRESSED = reserviert
ENCRYPTED  = reserviert
FRAGMENTED = optional
```

---

# 10. Heartbeat Compact Frame

Für UDP-Heartbeat darf ein kleiner Spezialframe genutzt werden.

## 10.1 Heartbeat v1 Compact

```text
Offset  Size  Field
0       2     Magic = "LN"
2       1     Version
3       1     MessageType = 0x01
4       2     WorkerID
6       1     SlotID
7       1     SlotFlags
8       1     Load
9       1     QueueDepth
10      1     Sequence
11      1     CRC8
```

Gesamtgröße:

```text
12 Byte
```

---

# 11. SlotFlags

```text
Bit 0 = alive
Bit 1 = ready
Bit 2 = busy
Bit 3 = degraded
Bit 4 = error
Bit 5 = thermal_limit
Bit 6 = model_loading
Bit 7 = reserved
```

Beispiele:

```text
0b00000011 = alive + ready
0b00000111 = alive + ready + busy
0b00101011 = alive + ready + degraded + model_loading
```

---

# 12. Load Byte

```text
0       idle
1-100   Auslastung in Prozent
101-199 reserviert
200     unknown
250     offline / scheduler-set
255     invalid
```

Alternative Interpretation später möglich:

```text
High Nibble = Compute Load 0-15
Low Nibble  = Memory Pressure 0-15
```

---

# 13. QueueDepth

```text
0-254 = Queue-Tiefe
255   = Overflow / >=255
```

---

# 14. Register Payload

REGISTER nutzt den Common Header + Payload.

Payload v1:

```text
Field           Type
node_name       string
worker_id       uint16
slot_count      uint8
slots           array
```

Slot-Eintrag:

```text
slot_id         uint8
slot_type       uint8
model_name      string
device          string
capabilities    uint32
```

Slot Types:

```text
0x01 INSTRUCT
0x02 CODER
0x03 EMBEDDING
0x04 CLASSIFIER
0x05 SUMMARIZER
0x06 VALIDATOR
```

---

# 15. Task Payload

TASK nutzt TCP.

```text
task_id         uint32 / uuid mapped
attempt_id      uint8
task_type       uint8
timeout_ms      uint16
max_tokens      uint16
temperature     float32
input_length    uint32
input           bytes / utf-8
```

Task Types:

```text
0x01 instruct
0x02 code
0x03 summarize
0x04 classify
0x05 validate
0x06 edge_text_eval
```

---

# 16. Result Payload

```text
task_id         uint32
attempt_id      uint8
status          uint8
duration_ms     uint32
output_length   uint32
output          bytes / utf-8
```

Status:

```text
0x00 ok
0x01 rejected
0x02 timeout
0x03 model_error
0x04 invalid_input
0x05 degraded
```

---

# 17. Error Payload

```text
error_code      uint16
severity        uint8
message_length  uint16
message         utf-8
```

Fehlerklassen:

```text
1000 protocol_error
1001 crc_error
1002 unsupported_version
1003 unknown_message_type
1004 invalid_payload

2000 model_not_ready
2001 model_load_failed
2002 inference_failed
2003 tokenizer_failed
2004 device_unavailable

3000 timeout
3001 no_slot_available
3002 consensus_failed
```

---

# 18. Embedding Service

Der Embedding-Service kann über LiNeP oder HTTP angesprochen werden.

Primär:

```text
LiaraEmbeddingService.exe
```

Nutzung:

```text
Orchestrator Vorfilter
RAG-Kandidaten
Math/Plausibilität
Scheduler Consensus
Edge-Textauswertung
```

Python bleibt:

```text
Fallback / Degraded Path
```

---

# 19. Similarity Request

```text
left_length      uint32
left_text        utf-8
right_length     uint32
right_text       utf-8
threshold        float32
```

Response:

```text
similarity       float32
accepted         uint8
dims             uint16
duration_ms      uint32
```

---

# 20. Consensus Request

Der Scheduler sendet drei Kandidaten an den Embedding-Service.

```text
task_id          uint32
candidate_count  uint8
threshold        float32
candidate_1_len  uint32
candidate_1      utf-8
candidate_2_len  uint32
candidate_2      utf-8
candidate_3_len  uint32
candidate_3      utf-8
```

---

# 21. Consensus Response

```text
task_id          uint32
consensus_level  uint8
best_index       uint8
sim_ab           float32
sim_ac           float32
sim_bc           float32
accepted         uint8
```

Consensus Level:

```text
0 = failed
1 = partial 2/3
2 = strong  3/3
```

Regel:

```text
2/3 >= 0.99 = akzeptiert
3/3 >= 0.99 = high confidence
```

---

# 22. Scheduler-Auswahl

Ein Slot wird ausgeschlossen, wenn:

```text
alive == false
ready == false
error == true
model_loading == true
heartbeat_age > timeout
queue_depth >= max_queue
load >= max_load
cooldown aktiv
```

Score:

```text
score =
  load * 1.0
+ queue_depth * 10.0
+ avg_latency_ms * 0.02
+ busy_penalty
+ degraded_penalty
+ thermal_penalty
```

Empfohlen:

```text
busy_penalty     = 20
degraded_penalty = 50
thermal_penalty  = 100
```

---

# 23. Triple Inference

Für kritische Aufgaben:

```text
Task
  -> Helper A
  -> Helper B
  -> Helper C
  -> Scheduler
  -> Embedding Consensus
  -> Orchestrator
  -> Primary Liara
```

Fast-Fail:

```text
Wenn zwei Ergebnisse vorhanden sind
und Similarity >= threshold
darf früh akzeptiert werden.
```

---

# 24. Timeouts

Empfehlung:

```text
Heartbeat interval    1-2 s
Heartbeat timeout     3-5 s
Task timeout helper   300-1500 ms für kleine Tasks
Coder timeout         höher, z. B. 5-15 s
Consensus timeout     500-1500 ms
```

---

# 25. Retry

Jeder Task nutzt:

```text
task_id
attempt_id
```

Regel:

```text
max_attempts = 2
```

Beispiel:

```text
attempt 1 -> Slot A
timeout
attempt 2 -> Slot B
```

---

# 26. Redundanz

Worker kennen keine Partner.

Nur der Scheduler kennt Gruppen.

```text
RedundancyGroup instruct-A
  Slot A
  Slot B
  Slot C
```

Bei Triple-Inference werden bevorzugt drei Slots aus unterschiedlichen Workern gewählt.

---

# 27. Security v1

LiNeP v1 definiert noch keine vollständige Verschlüsselung.

Pflicht für v1-Implementierungen:

```text
nur erlaubte IPs
feste WorkerIDs
CRC prüfen
Version prüfen
MessageType prüfen
PayloadLength begrenzen
Timeouts erzwingen
keine Ausführung durch Heartbeat
```

Optional später:

```text
HMAC
mTLS
Noise Protocol
Session Keys
```

---

# 28. Payload Limits

Empfohlen:

```text
UDP Compact Heartbeat: 12 Byte
UDP Common Frame: <= 512 Byte
TCP Task Payload: konfigurierbar
Default max payload: 1 MB
```

---

# 29. State Machine

Slot-Zustände:

```text
INIT
REGISTERED
LOADING
READY
BUSY
DEGRADED
ERROR
OFFLINE
```

Übergang:

```text
INIT -> REGISTERED -> LOADING -> READY
READY -> BUSY -> READY
READY -> DEGRADED
ANY -> ERROR
ANY -> OFFLINE bei Timeout
```

---

# 30. Diagnose

Für Debug darf zusätzlich HTTP/JSON existieren.

```text
GET /health
GET /ready
GET /status
GET /slots
```

Regel:

```text
LiNeP für Runtime.
HTTP für Diagnose.
```

---

# 31. DLL-Modell

Helper können Modelle per DLL bereitstellen.

```text
LiaraHelper.exe
  plugins/
    instruct.dll
    coder.dll
```

Minimal Interface:

```cpp
extern "C" __declspec(dllexport)
bool liara_init();

extern "C" __declspec(dllexport)
bool liara_infer(const char* input, char* output);

extern "C" __declspec(dllexport)
void liara_shutdown();
```

Empfohlenes Interface:

```cpp
struct LiaraRequest {
    const char* input;
    int max_tokens;
    float temperature;
};

struct LiaraResponse {
    char* output;
    int status;
};

extern "C" __declspec(dllexport)
bool liara_infer(LiaraRequest* req, LiaraResponse* res);
```

---

# 32. Referenzdienste

```text
LiaraHelperInferServer.exe
  Instruct / Coder auf NPU

LiaraEmbeddingService.exe
  Embedding / Similarity

LiaraHelperScheduler.exe
  Slot-Auswahl / Triple-Consensus

Liara Primary
  finale Interpretation
```

---

# 33. Design Inspiration

LiNeP ist inspiriert von industriellen Feldbussen wie EtherCAT.

Übernommen:

```text
kompakte Zustandsdaten
zyklische Heartbeats
feste Datenstrukturen
deterministische Auswertung
Slot-/Prozessdaten-Denken
```

Nicht übernommen:

```text
Layer-2-Zwang
physische Topologie
harte Echtzeit
Master-Slave-Begriff
```

---

# 34. Terminologie

Nicht verwenden:

```text
Master / Slave
```

Verwenden:

```text
Scheduler / Worker
Control Plane / Data Plane
Orchestrator / Helper
Slot / Node
```

---

# 35. Leitsätze

```text
UDP ist Zustand.
TCP ist Auftrag.
```

```text
Worker melden.
Scheduler verteilt.
Embedding misst.
Liara entscheidet.
```

```text
Ein Ergebnis ist Meinung.
Zwei sind Hinweis.
Drei mit Konsens sind belastbar.
```

```text
LiNeP verbindet Knoten.
Liara versteht Bedeutung.
```

---

# 36. Zielzustand v1.0

LiNeP v1.0 gilt als erreicht, wenn:

```text
[ ] Worker registrieren sich
[ ] Heartbeat läuft binär
[ ] Scheduler erkennt Slots
[ ] Tasks werden per TCP verteilt
[ ] Results kommen zurück
[ ] Embedding-Service prüft Similarity
[ ] Triple-Consensus funktioniert
[ ] Timeouts/Retry funktionieren
[ ] HTTP bleibt nur Diagnose/Fallback
[ ] Python ist nicht mehr im Hot Path
```

---

# 37. Cross-Compile Anforderungen

LiNeP-Implementierungen müssen auf vier Zielplattformen bauen:

```text
Windows  x64    (MSVC / Clang-cl)
Windows  ARM64  (MSVC / Clang-cl)
Linux    x64    (GCC / Clang)
Linux    ARM64  (GCC / Clang, Crosscompile-Toolchain)
```

---

## 37.1 Build-System

```text
CMake >= 3.20
C++17 minimum
kein plattformspezifischer Code außerhalb der PAL-Schicht
```

Toolchain-Dateien liegen in:

```text
cmake/toolchains/
  windows-x64.cmake
  windows-arm64.cmake
  linux-x64.cmake
  linux-arm64.cmake
```

---

## 37.2 Platform Abstraction Layer (PAL)

Alle plattformabhängigen Operationen werden in einer PAL-Schicht gekapselt.

```text
linep/pal/
  socket.hpp       <- abstrakte Socket-API
  socket_win.cpp   <- Winsock2-Implementierung
  socket_posix.cpp <- BSD-Socket-Implementierung
  thread.hpp       <- abstrakte Thread/Mutex-API
  clock.hpp        <- monotone Zeitquelle
  byteorder.hpp    <- Endian-Hilfsfunktionen
```

Öffentliche API darf nie direkt `SOCKET`, `HANDLE`, `pthread_t` o.ä. exponieren.

---

## 37.3 Endianness

LiNeP-Wire-Format ist immer **Little-Endian** (x64/ARM64-nativ).

```text
Alle Multi-Byte-Felder: Little-Endian
float32: IEEE 754, Little-Endian
```

Auf Big-Endian-Systemen (falls zukünftig relevant):

```text
byteorder.hpp stellt le16, le32, le_float Hilfsfunktionen bereit
```

---

## 37.4 Integer-Typen

Ausschließlich fixed-width types:

```cpp
#include <cstdint>

uint8_t   uint16_t   uint32_t   uint64_t
int8_t    int16_t    int32_t    int64_t
```

Verboten im Protokoll-Code:

```text
int   long   unsigned   size_t   DWORD   WORD   BOOL
```

---

## 37.5 Struct-Layout

Alle Header-Structs müssen packed sein:

```cpp
#pragma pack(push, 1)
struct LiNePHeader { ... };
#pragma pack(pop)
```

Alternativ per GCC/Clang-Attribut:

```cpp
struct __attribute__((packed)) LiNePHeader { ... };
```

Cross-kompatibler Wrapper in `linep.hpp`:

```cpp
#if defined(_MSC_VER)
  #define LINEP_PACKED_BEGIN  __pragma(pack(push,1))
  #define LINEP_PACKED_END    __pragma(pack(pop))
  #define LINEP_PACKED
#else
  #define LINEP_PACKED_BEGIN
  #define LINEP_PACKED_END
  #define LINEP_PACKED        __attribute__((packed))
#endif
```

---

## 37.6 Socket-Abstraktion

```cpp
// linep/pal/socket.hpp
namespace linep::pal {

using SocketHandle = /* opaque */;

SocketHandle udp_create();
SocketHandle tcp_connect(const char* host, uint16_t port);
SocketHandle tcp_listen(uint16_t port);
SocketHandle tcp_accept(SocketHandle server);

int  send_all(SocketHandle s, const uint8_t* buf, int len);
int  recv_all(SocketHandle s, uint8_t* buf, int len);
void close_socket(SocketHandle s);

void socket_init();   // WSAStartup auf Windows, no-op auf Linux
void socket_cleanup();

} // namespace linep::pal
```

---

## 37.7 Linux ARM64 Crosscompile

Toolchain für Linux ARM64 von Windows oder Linux x64:

```cmake
# cmake/toolchains/linux-arm64.cmake
set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)

set(CMAKE_C_COMPILER   aarch64-linux-gnu-gcc)
set(CMAKE_CXX_COMPILER aarch64-linux-gnu-g++)

set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
```

Aufruf:

```sh
cmake -B build-arm64 -DCMAKE_TOOLCHAIN_FILE=cmake/toolchains/linux-arm64.cmake
cmake --build build-arm64
```

---

## 37.8 Abhängigkeiten

LiNeP hat **keine externen Abhängigkeiten** im Core.

```text
Core:   nur C++17 stdlib + OS-Netzwerk-API
Tests:  optional catch2 (header-only)
Tools:  optional nlohmann/json für Diagnose-HTTP
```

Kein ZMQ. Kein Boost. Kein Protobuf. Kein gRPC.

---

## 37.9 CI-Matrix (Ziel)

```text
Platform          Compiler      Arch    Status
Windows           MSVC 19.x     x64     [ ]
Windows           MSVC 19.x     ARM64   [ ]
Linux             GCC 12+       x64     [ ]
Linux (cross)     GCC arm64     ARM64   [ ]
Linux             Clang 17+     x64     [ ]
```
