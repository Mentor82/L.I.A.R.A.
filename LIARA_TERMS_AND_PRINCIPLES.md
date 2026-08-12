# LIARA — Terms and Principles

Stand: 2026-08-11

## Kernbegriffe

| Begriff | Bedeutung in LIARA |
| --- | --- |
| LIARA-DNA | Gemeinsame Architekturprinzipien aller Instanzen; nicht identischer Code oder identische Aufgabe. |
| Instanz | Konkrete laufende oder paketierte Auspraegung einer LIARA-Rolle. |
| Worker | Spezialisierte, begrenzt eigenstaendige ausfuehrende Instanz. |
| Tool | Deterministische, contract- und policy-gesteuerte Operation. |
| SYS | Kanonischer operativer Toolpfad fuer strukturierte direkte Befehle. |
| Orchestrator | Koordiniert Routing und Workflows; besitzt nicht automatisch alle Rollen. |
| InputSituationProfile | Typisiertes Profil der Nachricht: Kontext, Domaene, Mood, Unsicherheit, Handlungs- und Planungsbedarf. |
| Mood | Eingangs- und Kommunikationssignal; keine Behauptung menschlicher Emotion oder Bewusstheit. |
| Context | Fuer den aktuellen Auftrag aktiv verdichtete Information. |
| Memory | Persistierter, mehrschichtiger Zustand mit Provenienz und Lifecycle. Kontext ist nicht Memory. |
| Evidence | Beleg oder Signal fuer eine Aussage oder Entscheidung. |
| Validator | Prueft Regeln, Struktur, Contracts und Ausgabefaehigkeit. |
| Judge | Bewertet Qualitaet, Plausibilitaet und Zielerreichung. |
| Governance | Bestimmt die erlaubte Tragweite einer Aenderung oder Ausfuehrung. |
| Simulation | Isolierte Pruefung eines Ablaufs oder moeglichen Zustands. |
| Dreaming | Erzeugung und Bewertung von Proposals, nicht von ungeprueften Produktivmutationen. |
| Workspace | Eingegrenzter realer WSL-Arbeitsraum, nicht der kanonische Windows-Projektroot. |
| Mutation Verification | Read-after-write, Stat, Hash oder Diff als Nachweis einer Zustandsaenderung. |
| LiNeP | Ressourcen-/Worker-Netzgedanke mit Scheduler-, Slot- und Heartbeat-Komponenten; global teilweise integriert. |
| Helper | Spezialisierter Runtime-Helfer, etwa fuer NPU-Offload; kein globaler Scheduler. |
| Heartbeat | Spaeteres Lebens-/Kapazitaetssignal fuer Verfuegbarkeit, Last, Queue, Temperatur und Energie. |
| Voice Identity | DDNA-Beschreibung von Liaras Stimme; besitzt weder Codec noch Transportformat. |
| SpeechPlan | Semantische, formatunabhaengige Folge aus Text, Rolle, Prosodiehinweis und Pause. |
| AudioStream | Zustandsbehaftete, geordnete und abbrechbare binaere Audioausgabe mit Backpressure. |
| AudioArtifact | Persistente, adressierbare Audioausgabe; vom laufenden AudioStream getrennt. |

## Gemeinsame architektonische DNA

1. Komponenten werden durch Beziehungen und Fluesse verstanden.
2. Erzeugen, Pruefen, Bewerten, Entscheiden, Ausfuehren und Speichern bleiben
   logisch getrennt.
3. Kommunikation erfolgt ueber typisierte Contracts und Schemas.
4. Modelle sind austauschbar; Tools sollen deterministisch sein.
5. Berechtigungen sind explizit, minimal und policy-gated.
6. Entscheidungen und Mutationen muessen auditierbar sein.
7. Eine behauptete Mutation ist ohne beobachtete Evidenz kein Erfolg.
8. Anpassungen sollen reversibel sein; Rollback ist Teil des Designs.
9. Selbstpruefung ist erlaubt, Selbstfreispruch nicht.
10. LIARA darf stabilisieren, aber nicht eigenmaechtig ihre Verfassung oder
    gemeinsame DNA veraendern.

## Denk- und Handlungsmodell

```text
Analysieren -> Denken -> Antworten -> Planen -> Handeln
```

Dies ist keine starre lineare Pipeline. Das Eingangssituationsprofil
entscheidet, welche Teile benoetigt werden. Eine einfache Frage kann direkt
beantwortet werden; ein komplexer Workspace-Auftrag benoetigt Plan,
Ausfuehrung, Beobachtung und Validator.

## Ressourcen- und Entscheidungsprinzip

```text
s = (c, m, g)

C(a) = alpha*depth + beta*tokens + gamma*tools + delta*entropy
U(a) = goal_progress - C(a)
a*   = argmax U(a), mit C(a) <= C_max
```

Fibonacci ist kein magischer Entscheider, sondern eine moegliche
Wachstums-/Rekursionsgrenze. Plausibilitaet, Utility, Risiko, Confidence,
Information Gain und reale Ressourcen bestimmen gemeinsam, welcher Ast
weiterverfolgt wird.

Leitregel:

> Jeder weitere Schritt muss Kontext transformieren oder Zielprogress
> erzeugen — nicht nur Kontext und Moeglichkeiten anhaeufen.

Mathematische Referenz:
[`MIRKO_MATHE_KONSOLIDIERT.md`](MIRKO_MATHE_KONSOLIDIERT.md).

## Sicherheitsprinzip fuer Selbstentwicklung

```text
lesen / analysieren / kopieren / simulieren / testen / vorschlagen
!=
Produktivcode aendern / Rechte erweitern / Governance veraendern
```

Freier Lesezugriff auf Quellcode ist kein freies produktives Aenderungsrecht.
Der erlaubte Eingriff richtet sich nach Risiko, Policy, Evidenz und
Governance-Freigabe.
