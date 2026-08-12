# Service: liara-self-observer

Stand: 2026-07-15

Code: `services/self_observer/`, `services/contracts/self_observer.py`  
Default-Port: `8060`

Die schnellen API-/Heartbeat-Probes verwenden standardmaessig
`LIARA_SELF_OBSERVER_TIMEOUT_SECONDS=4`. Fuer den tieferen aggregierten
Backend-Check gilt separat `LIARA_SELF_OBSERVER_BACKEND_TIMEOUT_SECONDS=12`.
So verlaengert ein langsamer Store-Check nicht das Fehlerbudget aller Quellen.
API-Liveness wird direkt ueber `:8010/health` gelesen; die Backend-Funktion
kommt direkt vom kanonischen Memory-Service `:8020/health/backends`. Der
Observer verwendet nicht den langsameren API-Doppelproxy als Evidenzquelle.

## Verantwortung

`liara-self-observer` ist eine eigenstaendige, zyklische Wahrnehmungsinstanz.
Der Observer selbst bleibt rein lesend und normalisiert drei Evidenzdomaenen:

```text
Resource Heartbeat -> hardware
API-/Backend-Health -> software
ai-validator-Artefakte -> assurance
```

Aus diesen Quellen entsteht ein versionierter `SystemStateEnvelope` mit
Zustand, Trend, Confidence, Stabilitaet, Signalen und Quellenherkunft. Der
Observer besitzt keine Tool-, Mutations-, Scheduler-, Helper-, LiNeP- oder
Dreaming-Ausfuehrungsrechte. Ein davon getrenntes Assurance-Gate darf eine
ai-validator-Pruefung nur nach expliziter Konfiguration einreichen.

## Zyklus und Ruhehysterese

Der Default-Zyklus betraegt 30 Sekunden. Ein einzelner ruhiger Messpunkt gilt
nicht als Leerlauf. Nur wenn Hardwarekapazitaet, Softwarezustand,
Validator-Evidenz, Confidence und Stabilitaet mehrere Zyklen tragen, wechselt
die Phase:

```text
observing -> quiet_candidate -> quiet_stable
```

`background_analysis_candidate=true` ist lediglich Evidenz fuer eine spaetere
Steuerinstanz. Findings, degradierte Quellen oder knappe Kapazitaet setzen den
Ruhezaehler zurueck.

## Assurance-Gate

Das Gate bewertet nach jedem Beobachtungszyklus:

```text
healthy + quiet_stable + background_analysis_candidate
+ Mindestabstand abgelaufen
+ explizite Berechtigung
-> typisierte SelfInspectionDecision
```

Die Modi sind bewusst getrennt:

- `disabled`: keine Freigabebewertung;
- `observe` (Default): zeigt `would_submit`, reicht aber keinen Job ein;
- `submit`: darf ueber den bestehenden Memory-Service genau einen
  ai-validator-Job einreichen.

Nach einer Einreichung verhindert der persistierte Mindestabstand standardmaessig
sechs Stunden lang einen weiteren Lauf. Der Request traegt Request-, Run-,
Session- und Quellenkontext. Ein fehlender Workspace, instabiler Zustand oder
nicht abgelaufener Mindestabstand blockiert die Einreichung. Das Gate fuehrt
weder Shellbefehle noch Patches aus und bewertet auch nicht sein eigenes
Validator-Ergebnis.

Dieses Self-Inspection-Gate ist vom Dreaming Proposal Assurance Gate getrennt:
Self Inspection entscheidet, ob und wann eine zyklische Systempruefung
eingereicht werden darf. Proposal Assurance bindet dagegen einen bereits
ausgefuehrten Validator-Report an genau ein Dreaming-Proposal und kann dessen
Approval blockieren. Beide nutzen dieselben Validator-Job-Contracts, vergeben
aber keine gegenseitigen Freigaben.

Ein eingereichter Job wird in Folgezyklen ueber die bestehenden Status- und
Result-Contracts weiterverfolgt:

```text
queued/running
-> validator/status
-> completed/failed
-> validator/result
-> strukturierte Findings + Artefakte
-> neue StateEvidence(domain=assurance)
-> naechster SystemStateEnvelope
```

Ein laufender Job erzeugt `attention`, damit kein zweiter Ruhezyklus parallel
freigegeben wird. Warnungen werden als `attention`, Fehler oder ein gescheiterter
Job als `degraded` zurueckgefuehrt. Ein befundfreier abgeschlossener Lauf wird
`healthy`. Auch diese Evidenz altert und wird danach sichtbar als `stale`
markiert. Damit schliesst sich der Pruefkreis, ohne dass Findings durch den
Observer aufgehoben werden koennen.

## Persistenz

Der lokale Store liegt standardmaessig unter `data/self_observer/`:

- `latest.json`: atomar ersetzter aktueller Zustand;
- `history.jsonl`: append-only Verlauf.

Beim Neustart werden Sequenz und Ruhezaehler aus `latest.json` fortgesetzt.
Die HTTP-History bleibt zusaetzlich auf maximal 240 Eintraege begrenzt.

## Endpunkte

- `GET /health`
- `GET /v1/state`
- `GET /v1/history?limit=1..240`
- `GET /v1/inspection`
- `POST /v1/inspection/canary` (default aus, Bearer-Token erforderlich)
- `GET /v1/status.txt`

Die zentrale API projiziert Zustand und Verlauf read-only ueber
`GET /operations/self-observer?history_limit=1..240`.

Der Canary-Endpunkt ist keine regulaere Steuerflaeche. Er muss mit
`LIARA_SELF_INSPECTION_CANARY_ENABLED=true`, einem nichtleeren Token und einem
Workspace explizit fuer genau den Testprozess aktiviert werden. Er darf einen
gesunden, aber nicht ruhigen Zustand nur als sichtbar markierten
`operator_canary` uebersteuern; Health, aktiver Job und Mindestabstand bleiben
Blocker. Eine eng begrenzte Ausnahme erlaubt die Erneuerung, wenn ausschliesslich
stale terminale Assurance-Evidenz den Zustand degradiert und Hardware sowie
Software healthy sind. Diese Recovery bleibt operatorautorisiert, rate-limited
und erzeugt einen regulaeren neuen Validator-Job. Nach dem Lauf wird der Dienst
wieder ohne Canary-Konfiguration im
Modus `observe` gestartet.

Ist der Recovery-Job selbst terminal fehlgeschlagen, darf ein neuer
Authorization-Identifier genau einen expliziten Retry vor Ablauf des normalen
Mindestabstands ausloesen, sofern weiterhin ausschliesslich Assurance
degradiert ist. Nach einem erfolgreichen Submit ist der Canary-Endpunkt fuer
die restliche Prozesslaufzeit verbraucht und antwortet auch mit demselben Token
mit HTTP 403.

```powershell
.\scripts\run_self_inspection_canary.ps1 -Token <ephemeral-token>
.\scripts\run_self_inspection_canary.ps1 -Token <ephemeral-token> -AllowStaleAssuranceRecovery
.\scripts\run_self_inspection_canary.ps1 -Token <ephemeral-token> -AllowFailedAssuranceRetry
.\scripts\run_self_inspection_canary.ps1 -ResumeExisting -AuthorizationId <id>
.\scripts\run_self_inspection_canary.ps1 -VerifyPersistenceOnly -AuthorizationId <id>
```

## Start und Test

```powershell
.\scripts\start_self_observer_instance.ps1
.\.venv\Scripts\python.exe -m pytest -q tests\unit\test_self_observer.py
```

## Noch offen

- reale Langzeitbaseline ueber unterschiedliche Last- und Ruhephasen;
- feinere Softwaremetriken fuer Latenz, Fehlerquote, Queue und aktive Arbeit;
- Live-Nachweis eines real eingereichten zyklischen Jobs nach expliziter Freigabe;
- feinere Severity-/Policy-Abbildung fuer unterschiedliche Validator-Checks;
- Begrenzung und Fortschrittsmodell des real zu breiten Validator-`quick`-Scopes;
- administrative Diagnoseansicht fuer Quellen und Ursachen;
- spaeterer, policy-gesteuerter Konsum durch Kontrollkreis oder Dreaming-Gate.
