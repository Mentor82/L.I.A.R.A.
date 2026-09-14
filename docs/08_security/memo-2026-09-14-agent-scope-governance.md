# Memo: Agent Scope Governance — Erfolg auf Objektebene kann Systemversagen sein

**Datum:** 2026-09-14  
**Status:** Architektur-/Governance-Memo  
**Bezug:** ADR-007 — AI-Brain Epistemic Subgraph & Visitor Pass Capability Paradigm  
**Thema:** Agentische Aufgabenerfüllung, Scope-Grenzen, Capability Governance

## Auslöser

Aus einer Diskussion über einen berichteten KI-Sicherheitsvorfall rund um Hugging Face entstand eine für L.I.A.R.A. wichtige Beobachtung:

> Eine KI kann ihre konkrete Aufgabe technisch mit Bravour erfüllen und das Gesamtsystem kann trotzdem versagen.

Wenn die Aufgabenstellung beispielsweise lautet, Schwachstellen zu finden, ist das Auffinden und Ausnutzen weiterer technisch erreichbarer Schwachstellen zunächst eine konsequente Fortsetzung dieses Ziels. Das Problem liegt dann nicht zwingend in der Leistungsfähigkeit des Agenten, sondern in der darüberliegenden Governance: Wo endet der erlaubte Scope, welche Fähigkeiten besitzt der Agent tatsächlich, und welche Übergänge müssen unabhängig vom Modell technisch blockiert werden?

## Kernerkenntnis

L.I.A.R.A. darf Sicherheit nicht daran koppeln, dass ein Modell eine sprachlich formulierte Grenze „versteht“ oder freiwillig einhält.

Es muss zwischen mindestens drei Ebenen unterschieden werden:

1. **Objektebene — Task Execution**  
   Der Agent löst die ihm gestellte Aufgabe möglichst gut.

2. **Metaebene — Scope & Capability Governance**  
   Ein unabhängiger Kontrollmechanismus bestimmt, welche Ressourcen, Werkzeuge, Systeme und Aktionen innerhalb dieser Aufgabe erlaubt sind.

3. **Meta-Metaebene — Verfassungs-/Policy-Grenzen**  
   Definiert Invarianten, die weder Agent noch Planner noch Tool-Orchestrator selbst erweitern oder überschreiben dürfen.

Ein Erfolg auf Ebene 1 darf niemals automatisch als Erfolg des Gesamtsystems interpretiert werden.

## Konsequenz für L.I.A.R.A.

Die zentrale Sicherheitsfrage lautet nicht:

> „Hat der Agent verstanden, dass er dort nicht hin darf?“

sondern:

> „Besitzt der Agent überhaupt eine technisch gültige Capability, um dort hinzugelangen?“

Daraus folgen für L.I.A.R.A. folgende Prinzipien:

- **Capabilities statt bloßer Prompt-Regeln.** Berechtigungen müssen technisch erzwungen und explizit vergeben werden.
- **Default Deny.** Nicht explizit autorisierte Ressourcen, Netze, Tools und Aktionen sind nicht verfügbar.
- **Visitor-Pass-Prinzip.** Externe oder temporäre Agenten erhalten minimal notwendige, zeitlich und sachlich begrenzte Rechte.
- **Scope ist Datenstruktur, nicht Text.** Zielsysteme, erlaubte Aktionen, Netzgrenzen und Ressourcen müssen maschinenprüfbar beschrieben werden.
- **Keine implizite Capability-Vererbung.** Ein Tool oder Sub-Agent darf aus einer erlaubten Fähigkeit keine weitergehende Berechtigung ableiten.
- **Egress muss kontrolliert werden.** Eine Sandbox ohne kontrollierten Netzwerkpfad ist keine belastbare Sicherheitsgrenze.
- **Planner und Solver trennen.** Der Solver soll leistungsfähig sein; der Planner/Governance-Layer entscheidet, was ausgeführt werden darf.
- **Judge ist nicht die letzte Sicherheitsinstanz.** Eine nachgelagerte Modellbewertung ersetzt keine technische Zugriffskontrolle.
- **Audit vor Autonomie.** Jede Capability-Nutzung muss nachvollziehbar, attribuierbar und überprüfbar sein.
- **Capability-Eskalation ist ein Ereignis.** Jede Erweiterung des erlaubten Scopes benötigt eine explizite Autorisierung und muss protokolliert werden.

## Sicherheitsinvariante

> **Ein Agent darf eine Aktion nur deshalb ausführen können, weil ihm die dafür erforderliche Capability explizit und überprüfbar zugewiesen wurde — niemals nur deshalb, weil die Aktion technisch erreichbar ist oder zur Optimierung seines Ziels nützlich erscheint.**

Formal gedacht:

```text
TaskSuccess != SystemSuccess

Allowed(action) :=
    TaskRequires(action)
    AND CapabilityGranted(agent, action, resource)
    AND ScopeContains(resource)
    AND PolicyAllows(action)
    AND RuntimeEnforces(all_above)
```

Das Modell darf `Allowed(action)` nicht selbst definieren oder erweitern.

## Bezug zu ADR-007

ADR-007 beschreibt bereits das Visitor-Pass-/Capability-Paradigma für externe AI-Agenten. Dieses Memo verschärft dessen Bedeutung:

Der Visitor Pass ist nicht nur eine praktische Zugriffskontrolle für externe Akteure. Er ist ein grundlegendes Muster für **jede agentische Ausführung innerhalb L.I.A.R.A.s**. Auch ein intern hochprivilegierter Solver sollte nur innerhalb eines explizit erzeugten Ausführungskontexts handeln.

Damit wird Capability Governance zur Brücke zwischen L.I.A.R.A.s epistemischer Architektur und ihrer operativen Sicherheit.

## Architekturfolgerung

Für zukünftige Agent-/Tool-Ausführung sollte ein Auftrag deshalb mindestens einen unveränderbaren Execution Envelope besitzen:

```text
ExecutionEnvelope
├── task_id
├── principal / agent_id
├── allowed_capabilities[]
├── allowed_resources[]
├── allowed_network_targets[]
├── denied_resources[]
├── max_delegation_depth
├── expiry / ttl
├── approval_context
├── audit_context
└── immutable_policy_ref
```

Sub-Agenten und Tools erhalten niemals mehr Rechte als dieser Envelope erlaubt. Delegation darf Rechte nur erhalten oder reduzieren, nicht erweitern.

## Kurzfassung

Der relevante Lerneffekt ist nicht, dass eine leistungsfähige KI „zu weit gegangen“ sei.

Der relevantere Lerneffekt lautet:

**Wenn ein Agent seine Aufgabe hervorragend erfüllt und dabei eine nicht vorgesehene Systemgrenze überschreiten kann, ist primär die Grenze falsch konstruiert.**

L.I.A.R.A. sollte deshalb leistungsfähige Solver nicht künstlich schwach machen, sondern ihre Handlungsmöglichkeiten über technische, überprüfbare und unveränderbare Capability-Grenzen kontrollieren.
