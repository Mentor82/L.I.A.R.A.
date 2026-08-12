# Security Boundaries

Stand: 2026-07-13

## Relevante Grenzen

LIARA hat mehrere explizite Sicherheits- und Policy-Grenzen:

- Sandbox-Root fuer Datei-/Toolzugriffe
- Attachment-Scan fuer Uploads
- Output-Sanitizer
- nicht-oeffentliche Toolfilter in der API
- Sys-Command-Policy und Sys-Audit
- Judge Pre-/Post-Action-Pruefungen
- Memory-Context-Upsert-Policy
- Safe Simulation Mode
- Native WSL-Session-Confinement

## API-Safety

`services/api/app.py` enthaelt Muster fuer offensichtlich schaedliche Cyber-Anfragen und gibt Refusal-Texte zurueck. Diese Logik ist eine Basisschutzschicht, nicht die einzige Policy.

## Tool-Grenzen

Die aktuelle public Tool-Oberflaeche ist klein und bewusst eingeschraenkt:

- `sys`
- `orientation`
- `plot_chart`
- `wsl_session`

Die API filtert aus der oeffentlichen Toolliste unter anderem:

- `compute.run`
- `compute.generate`
- `read_file`
- `list_files`
- `web_search`

Historische Direkttools wie `fetch`, `current_time` und `session_context` gehoeren ebenfalls nicht mehr zur regulaeren public Tool-Flaeche.

Riskante Aktionen wie `sys`, `/sys`, `compute.run` und `compute.generate` werden im Orchestrator/Judge-Kontext besonders behandelt.

## Datei- und Sandbox-Grenzen

Relevante Module:

- `services/shared/sandboxing.py`
- `services/shared/attachment_security.py`
- `services/shared/output_sanitizer.py`

Uploads laufen ueber Dateiname-Sanitizing, Attachment-Scan und Sandbox-Pfade.

## Native WSL-Session-Grenze

Die WSL-Session-Runtime erweitert die Sandbox um eine explizite
Entwicklungsgrenze:

- Der lokale Projektroot ist die kanonische Quelle.
- Secrets, `.env`, Caches, Modelle, Backups, Build- und Laufzeitartefakte werden
  nicht in den Snapshot aufgenommen.
- `source` wird in WSL read-only gesetzt; nur `work` ist veraenderbar.
- Session-IDs und Zielpfade werden auf den konfigurierten Session-Root
  eingegrenzt.
- Kommandos werden nicht als freie Shellstrings eingefuehrt, sondern laufen
  ueber die bestehende `sys`-Policy mit direkter Argumentliste.
- Ein caller-spezifiziertes Arbeitsverzeichnis ausserhalb der registrierten
  Session wird abgelehnt.
- Writes gelten erst nach erneuter Zustandspruefung als erfolgreich.
- `collect` erzeugt Patch, Kandidat, Snapshot-/Patch-/Kandidaten-Hashes und
  Trace-Daten; es schreibt nicht in den kanonischen Root zurueck.
- Snapshot-, Datei- und Patchgroesse besitzen harte Obergrenzen.
- `destroy` entfernt nur den registrierten WSL-Session-Root. Lokale Audit- und
  Kandidatenartefakte bleiben erhalten.

Die erste Implementierung verwendet den WSL-Nutzer `liara` und isoliert ueber
eigene Session-Verzeichnisse und Berechtigungen. Separate temporaere OS-Nutzer
sind eine spaetere Haertung, sobald Julia und weitere Toolchains aus dem
privaten Home des Hauptnutzers herausgeloest sind.

## Memory-Policy

`services/memory/store.py` blockiert Context-Upserts bei:

- leerem Inhalt
- erkannten Secrets oder Tokens
- unvalidiertem `working_context`
- fehlendem Scope bei strikt markiertem Working Context

## Betriebssicherheit

Die FastAPI-Flächen besitzen derzeit keine durchgängige Authentisierung oder
TLS-Terminierung. Sie sind als lokale Dienste zu betreiben und dürfen ohne
vorgeschaltete Auth-/Netzwerkgrenze nicht auf untrusted Netze exponiert
werden.

SYS-Governance-Proposals und Decisions sind implementiert. Harte Durchsetzung
ist jedoch opt-in: Ohne `LIARA_SYS_GOVERNANCE_MODE=risk_based|all` oder den
kompatiblen Alt-Schalter `LIARA_SYS_GOVERNANCE_ENFORCE=1` bleibt
die Command-/Argument-Policy die unmittelbare Grenze, aber eine Proposal-ID ist
nicht für jeden SYS-Aufruf erforderlich.

Auch bei `risk_based` wird die W/G/B-Policy nicht durch eine pauschale
Netzwerkklassifizierung ersetzt. Ein kontextuell validierter, rein lesender
HTTP(S)-`curl` ist Liaras erlaubte Web-Fähigkeit. Uploads, schreibende
Methoden, Credentials, Proxy- und unsichere URL-/TLS-Varianten bleiben durch
die Blacklist nicht approvable. Mutation, freie Codeausführung und
unprofilierter Netzwerkzugriff bleiben governance-pflichtig.

Web-Discovery erteilt keine implizite Folgefreigabe. Eine Inferenz darf
Informationsziel, Suchanfrage und moegliche Quelle bestimmen, aber keine
Policyentscheidung ersetzen. Suchseitenresultate sind mit
`evidence_scope=discovery` markiert und duerfen keine Fachfakten erden. Jede
anschliessend ausgewaehlte Ziel-URL wird als neue konkrete Aktion gegen
URL-Sicherheit, W/G/B, Pre-Action-Judge, Governance-Modus und SYS-Audit
geprueft. Es existiert keine fachliche Quellen-Schlagwortliste; der aktuell
verwendete Bing-RSS-Endpunkt ist ausschliesslich technische Discovery-
Infrastruktur. Siehe ADR-005.

Im `risk_based`- und `all`-Modus wird eine blockierte Workspace-Aktion nicht
still verworfen oder vom Agenten umgangen. Der Workspace-Agent erzeugt ein an
Kommando und Parameter gebundenes Pending-Proposal, markiert Step und Run als
`awaiting_decision` und stoppt den Plan vor dem naechsten Schritt. Ein
deterministischer Handoff-Key verhindert doppelte Pending-Proposals fuer
denselben Run, Step und Action-Digest. Approval autorisiert weiterhin nur die
gebundene Einzelaktion; sie bedeutet keine pauschale Freigabe des restlichen
Plans.

Der Handoff enthaelt inzwischen einen versionierten Resume-Checkpoint mit
Originalplan, aktuellem Step-Index, bereits verifizierten Ergebnissen,
Completed-Set, Math-Decision und Trace-Bindung. Nach Approval fuehrt die API
zuerst ausschliesslich die per SHA-256 gebundene Einzelaktion ueber denselben
Single-use-Invoke-Contract aus. Nur ein erfolgreiches und fuer Mutationen
verifiziertes Resultat darf den Agenten bei `step_index + 1` fortsetzen.
Fruehere Schritte werden nicht wiederholt. Reject, Digest-Abweichung,
inkonsistenter Checkpoint und Approval-Replay stoppen vor weiterer Ausfuehrung.
Ein spaeter erneut governance-pflichtiger Schritt erzeugt ein neues Proposal
und pausiert den fortgesetzten Plan erneut.

Der Live-Canary `sys-prop-2ba15f6933ca` bestaetigte diesen Vertrag am
2026-08-08 mit der Ereigniskette `proposal_created -> proposal_decided ->
invocation_attempted -> invocation_completed -> workspace_resume_completed`.
Die gebundene Aktion wurde genau einmal erfolgreich ausgefuehrt, der
Workspace-Validator schloss ohne Findings ab und ein erneuter Approval-Versuch
wurde mit HTTP 409 abgewiesen.

Fuer allgemeine, nicht als Workspace-Checkpoint erzeugte Proposals trennt
`POST /tools/sys/governance/actions` Approval, Apply und Rollback. Apply bleibt
an Original-Proposal, Action-Digest und Invocation-Limit gebunden. Vor dem
Overwrite einer vorhandenen Datei unter `LIARA_AGENT_WORKSPACE_ROOT` wird der
alte Inhalt bis maximal `LIARA_SYS_ROLLBACK_MAX_BYTES` lokal persistiert und
per SHA-256 gebunden. Rollback erzeugt eine separate kompensierende Proposal,
verbraucht deren Single-use-Slot und gilt erst nach verifiziertem alten Hash
als abgeschlossen. Parallele oder wiederholte Apply-/Rollback-Anforderungen
werden ueber persistierte Transaktionszustaende abgewiesen.

Dieser Vertrag verspricht keine allgemeine Reversibilitaet. In Version 1 ist
nur `tee`-Overwrite einer bereits vorhandenen verwalteten Workspace-Datei
rueckrollbar. Neue Dateien, Append, Verzeichnisse, Dependency-Installationen,
Netzwerkzugriffe und Codeausfuehrung bleiben explizit `supported=false`.
Snapshot-Artefakte koennen sensiblen Vorzustand enthalten und muessen wie
lokale Governance-Evidenz geschuetzt und durch spaetere Retention begrenzt
werden.

Der Live-Canary `sys-prop-e8c94737d54a` bestaetigte Apply und Rollback am
2026-08-08. Die Child-Proposal `sys-prop-4eaf029a3a44` wurde genau einmal
ausgefuehrt, der alte Inhalts-Hash wiederhergestellt und ein zweiter Rollback
mit HTTP 409 blockiert.

Die Workspace-Dependency-Recovery erlaubt keinen allgemeinen Package-Manager-
Zugriff. Nur einfache, allowlistete Paket-Spezifikationen dürfen über
`venv-pip` non-interaktiv in die Workspace-`.venv` installiert werden. URLs,
VCS-Quellen und lokale Paketpfade sind geblockt; der Installationsschritt wird
als Netzwerk- und Umgebungsmutation auditiert.

Compose-Datei enthaelt lokale Default-Passwoerter fuer Entwicklungsbetrieb:

- Postgres: `liara/liara2026`
- Redis: `liara2026`
- Neo4j: `neo4j/liara2026`

Diese Werte sind fuer lokale Entwicklung dokumentiert und duerfen nicht unveraendert in produktionsnahe Umgebungen uebernommen werden.

## Aktueller Befund

Die Sicherheitsgrenzen sind im Code real vorhanden, aber verteilt. Fuer neue riskante Tools sollte die Reihenfolge sein:

1. Tool-Contract definieren.
2. Policy/Sandbox-Regel ergaenzen.
3. Judge-/Audit-Pfad anbinden.
4. Unit- und Live-Safety-Test ergaenzen.
