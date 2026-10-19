# Service: liara-heartbeat

Stand: 2026-07-15

Code: `services/heartbeat/`, `services/contracts/heartbeat.py`  
Default-Port: `8050`

## Verantwortung

`liara-heartbeat` ist eine eigenstaendige LIARA-Instanz zur Beobachtung des
lokalen Laufzeitknotens. Sie normalisiert Messungen, fuehrt eine begrenzte
Zeitreihe und leitet daraus Zustandskurve, Signages und eine konservative
Ressourcenhuelle ab.

Sie ist weder Scheduler noch Helper und darf keine Prozesse starten, Arbeit
verteilen oder Policies veraendern.

## Kanonische Eingangsgrenze

Alle Quellen muessen `ObservationBatch` mit `ResourceObservation` erzeugen.
Erlaubte Ressourcen sind CPU, RAM, GPU, NPU, Batterie, Thermal, Power und
System. Messgroessen und Einheiten sind fest typisiert; Ratio-Werte liegen
immer zwischen 0 und 1, Zeitstempel sind zeitzonenbehaftet.

Quellenadapter:

- `NativeSystemAdapter`: direkte portable Messung ueber `psutil`;
- `JsonObservationAdapter`: bereits kanonisches JSON;
- `MappedCsvAdapter`: konfigurierbare Spaltenabbildung fuer beliebige
  CSV-Exporter, einschliesslich HWiNFO-Sensorlogs.

HWiNFO-Feldnamen duerfen nur in der Mapping-Konfiguration vorkommen. Sie
werden weder gespeichert noch an Scheduler, LiNeP oder Frontend weitergegeben.
Ein Beispiel liegt unter `config/heartbeat_csv_mapping.example.json`.

## Ausgaben

```text
ResourceObservation[]
-> HeartbeatSnapshot (aktueller Zustand)
-> StateCurve (Fenster, Gradient, Stabilitaet, Confidence)
-> ResourceEnvelope (Kapazitaet, Budgets, Parallelitaet)
```

Die Ressourcenhuelle ist Evidenz fuer einen spaeteren Scheduler, kein
Ausfuehrungsmandat.

Jede Metrikkurve enthaelt neben aktuellem Wert, Minimum, Maximum und Gradient
eine begrenzte Reihe typisierter Zeitpunkte. Dadurch kann eine Darstellung die
tatsaechliche Zustandskurve zeichnen, ohne Rohdaten oder herstellerspezifische
Sensornamen zu kennen.

## Operations- und Frontend-Grenze

Die zentrale LIARA-API liest Snapshot und Kurve ueber die konfigurierte
Heartbeat-Service-URL und stellt beides unter
`GET /operations/heartbeat?window_seconds=10..900` als eine no-store Antwort
bereit. Die Living Architecture Map fragt nur diesen API-Pfad ab.

Bei Auswahl von `Resource Heartbeat` zeigt das Detailpanel:

- eine einzelne, aus den beobachteten Auslastungswerten abgeleitete
  Systempulskurve;
- den aktuellen Zustand, Trend, die Sequenz und Frische des Signals.

Die Visualisierung erzeugt keine Ersatzwerte und besitzt keine Scheduler- oder
Ausfuehrungsrechte. Einzelmetriken, Adapterdiagnose, Signages, Confidence und
Ressourcenbudgets gehoeren in eine getrennte administrative Ansicht und werden
in der Architekturkarte bewusst nicht ausgebreitet.

## Sicherheit

- Lesende Antworten verwenden `Cache-Control: no-store`.
- Externe Ingestion ist per Default deaktiviert.
- Bei Aktivierung ist ein separater Bearer-Token erforderlich.
- Batches eines anderen `node_id` werden abgewiesen.
- Ein fehlender oder veralteter Sensorpfad wird als `unknown`/`heartbeat_stale`
  sichtbar und nicht als gesunder Zustand interpretiert.

## Start und Test

```powershell
.\scripts\start_heartbeat_instance.ps1
.\.venv\Scripts\python.exe -m pytest -q tests\unit\test_heartbeat_service.py
```

## API-Arbeitsmelder

Der native Adapter liest `GET http://127.0.0.1:8010/health/workload`
und liefert `system:liara-api:active_work` (`count`). Der API-Melder zaehlt
laufende HTTP-Arbeit bis zum Ende des ASGI-Aufrufs, einschliesslich Streams,
Fehlern und Abbruechen. Reine Health-/Heartbeat-/Observer-Abfragen zaehlen
nicht selbst als Arbeit. Der Quellzeitstempel bleibt erhalten; fehlende,
ungueltige oder ueber 15 Sekunden alte Daten werden nicht als Null gemeldet.
Der Observer nutzt diese kanonische Kurve mit seiner bestehenden Frischepolicy.

Die Abdeckung ist ausdruecklich `single_api_worker`: vorgesehen fuer die lokale
API mit einem Uvicorn-Worker. Dies ist keine globale Queue-/Cluster-Leerlaufgarantie.
Direkte Inferenz ausserhalb der API sowie Jobs nach dem Ende eines HTTP-Aufrufs
sind nicht enthalten. Vor Multiworker-/Queue-Betrieb ist ein aggregierter Melder
erforderlich; die Prozessmetrik darf dort nicht als globale Ruhefreigabe dienen.

## Noch offen

- HWiNFO-Livequelle konfigurieren oder einen kanonischen Export-Bridgeprozess
  anbinden;
- GPU-/NPU-spezifische Adapter ergaenzen;
- Heartbeat ueber LiNeP transportieren;
- Helper-/CoWorker-Mandate aus Scheduler-Entscheidungen ableiten;
- Zustandskurven mehrerer Instanzen zu einem gewichteten Systemzustand
  aggregieren;
- Liveadapter fuer zusaetzliche Sensorquellen dauerhaft betreiben und deren
  Herkunft/Frische in der Operationsansicht weiter aufschluesseln.
