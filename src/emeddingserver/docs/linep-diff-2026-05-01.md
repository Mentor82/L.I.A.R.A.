# LiNeP Source-Diff gegen LIARA-NPU-CLIENT

Stand: 2026-05-01

Verglichen wurden:

- Original / Source of Truth: `C:\ai\LIARA-NPU-CLIENT\liara-linep`
- Lokaler Snapshot: `C:\ai\LIARA\src\emeddingserver\linep`

Build-Verzeichnisse im Original (`build`, `build-ucrt64`) sind nicht Teil des fachlichen Vergleichs.

## Entscheidung

`C:\ai\LIARA-NPU-CLIENT\liara-linep` ist die Source of Truth fuer LiNeP.

Der lokale Snapshot unter `src/emeddingserver/linep` wurde am 2026-05-01 aus dieser Source of Truth aktualisiert. Build-Artefakte aus `build` und `build-ucrt64` bleiben ausgeschlossen.

Nach der Synchronisierung stimmen Dateiliste und SHA256-Hashes aller nicht-Build-Dateien zwischen Original und lokalem Snapshot ueberein.

## Urspruenglicher Diff

Vor der Synchronisierung war der lokale Snapshot aelter als das Original. Im lokalen Snapshot fehlten:

```text
src\tcp\tcp.cpp
src\tcp\tcp.hpp
tests\test_scheduler_selects_best_slots_fallback.cpp
tests\test_scheduler_selects_best_slots_with_diversity.cpp
```

Inhaltlich abweichend waren:

```text
CMakeLists.txt
README.md
include\linep\types.hpp
src\core\framing.cpp
src\core\framing.hpp
src\scheduler\scheduler.cpp
src\scheduler\score_engine.cpp
src\scheduler\score_engine.hpp
src\tcp\CMakeLists.txt
tests\CMakeLists.txt
```

## Relevante fachliche Aenderungen aus der Source of Truth

### Header / Framing

Das Original erweitert den LiNeP-Header:

- `HEADER_BASE_LEN = 24`
- `HEADER_BUILD_TIME_LEN = 6`
- `HeaderBuildTimeExt`
- `FLAG_BUILD_TIME`
- `header_len` darf jetzt groesser als 24 sein

Neue Helper:

```text
make_build_time_ext_from_build()
apply_build_time_extension()
try_parse_build_time_ext()
```

Die Build-Zeit wird per CMake als Compile-Definition gesetzt:

```text
LINEP_BUILD_YEAR_2D
LINEP_BUILD_MONTH
LINEP_BUILD_DAY
LINEP_BUILD_HOUR
LINEP_BUILD_MINUTE
LINEP_BUILD_SECOND
```

### TCP

Das TCP-Modul enthaelt jetzt echte Sender-/Receiver-Interfaces:

```text
linep::tcp::ITcpTaskSender
linep::tcp::ITcpTaskReceiver
create_task_sender()
destroy_task_sender()
create_task_receiver()
destroy_task_receiver()
```

Der Sender oeffnet pro Task eine TCP-Verbindung, sendet `TASK`, wartet auf `RESULT` und kopiert den Body in einen Caller-Puffer.

Der Receiver lauscht auf einem TCP-Port, nimmt `TASK`-Frames entgegen, ruft eine Callback-Funktion auf und sendet `RESULT` zurueck.

### Scheduler

Der Scheduler waehlt jetzt bis zu drei Slots:

```text
select_best_slots(..., k = 3)
```

Die Auswahl beruecksichtigt:

- harte Eligibility-Filter
- Score-Sortierung
- Worker-Diversity, maximal ein Slot pro `worker_id`

Der Scheduler dispatcht parallel auf mehrere Slots und beendet den Task beim ersten erfolgreichen Ergebnis. Wenn kein Ergebnis erfolgreich ist, wertet er Partial Results aus, requeued bei moeglichen Attempts oder meldet Timeout.

### Tests

Ergaenzte Tests:

- Multi-Slot-Auswahl mit Worker-Diversity
- Fallback-Auswahl ueber mehrere Slots

## Embeddingserver-Anpassung

Der lokale `embedding_linep.cpp` wurde nach der Synchronisierung an LiNeP v1.1 angepasst:

```text
Header 24 bytes
optional Extension bytes
Payload
```

Beim Empfangen wird nach dem Basis-Header zuerst `header_len - sizeof(Header)` gelesen und verworfen. Erst danach wird der Payload gelesen. Damit interpretiert der Embeddingserver Build-Time-Extension-Bytes nicht mehr faelschlich als JSON-Payload.

Beim Senden nutzt der Embeddingserver weiterhin Base-Header ohne Extension. Das ist kompatibel, weil LiNeP v1.1 `header_len = 24` weiterhin akzeptiert.

Langfristig sollte der Embeddingserver statt eigener TCP-Framing-Logik den neuen `linep::tcp::ITcpTaskReceiver` verwenden.

## Verifikation

```text
Dateiliste ohne Build-Artefakte: OK
SHA256-Vergleich ohne Build-Artefakte: OK
VC++ Direktbuild: OK
LiaraEmbeddingService.exe --help: OK
```
