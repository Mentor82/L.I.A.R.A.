# Memo: Plausibilität gilt nur unter Randbedingungen

**Datum:** 2026-09-17  
**Status:** Architektur-/Epistemik-Memo  
**Thema:** Plausibilität, Evidenz, Randbedingungen, physikalische Toleranzen, Reproduzierbarkeit

## Auslöser

Aus einer Diskussion über Reproduzierbarkeit beim Training kleiner KI-Modelle auf unterschiedlichen Hardware-/Backend-Kombinationen entstand eine allgemeine Beobachtung für L.I.A.R.A.:

> Eine Aussage kann innerhalb eines idealisierten Modells korrekt sein und unter realen Randbedingungen trotzdem nur eingeschränkt plausibel bleiben.

Beispiel: Zwei baugleiche, fehlerfrei arbeitende Rechensysteme sollen bei exakt gleicher deterministischer Berechnung dasselbe Ergebnis liefern. Das ist als Idealmodell korrekt. In realen Systemen existieren jedoch immer Randbedingungen: Temperatur, Versorgung, Taktverhalten, Firmware, Treiber, Scheduler, Kernelwahl, Parallelisierung, Floating-Point-Reihenfolge, Alterung, Bauteiltoleranzen und Messunsicherheit.

Wichtig ist dabei die Trennung:

- Physikalische Toleranzen bedeuten **nicht automatisch**, dass eine digitale Rechnung ein anderes Ergebnis liefert.
- Sie bestimmen aber die Bedingungen mit, unter denen die Annahme digitaler Deterministik belastbar ist.
- Abweichungen können außerdem aus Software-, Parallelitäts- oder Numerikpfaden entstehen, obwohl die nominelle Aufgabe identisch ist.

## Kernerkenntnis

L.I.A.R.A. sollte Aussagen nicht nur als `wahr/falsch` oder `bekannt/unbekannt` behandeln, sondern ihre **Gültigkeitsbedingungen** explizit mitführen.

Die Frage lautet nicht nur:

> „Ist Aussage X korrekt?“

sondern zusätzlich:

> „Unter welchen Randbedingungen ist X plausibel, wie stark ist X belegt, und welche Annahmen müssen gelten?“

## Epistemische Trennung

Für L.I.A.R.A. sind mindestens vier Dimensionen zu unterscheiden:

1. **Evidenz**  
   Welche Beobachtungen, Messungen oder Quellen stützen eine Aussage?

2. **Plausibilität**  
   Passt die Aussage unter den bekannten Randbedingungen zum restlichen Systemmodell?

3. **Konfidenz**  
   Wie sicher ist die aktuelle Bewertung angesichts Evidenz, Modellunsicherheit und fehlender Information?

4. **Gültigkeitsbereich / Randbedingungen**  
   Unter welchen Voraussetzungen darf die Aussage überhaupt angewendet werden?

Diese Dimensionen dürfen nicht miteinander gleichgesetzt werden.

Eine Aussage kann beispielsweise:

- stark belegt, aber nur in engem Gültigkeitsbereich plausibel sein,
- plausibel, aber schwach belegt sein,
- formal korrekt, aber unter realen Betriebsbedingungen unvollständig sein,
- unter idealisierten Bedingungen deterministisch und unter realen Bedingungen nur statistisch reproduzierbar sein.

## Beispiel: Reproduzierbarkeit

Idealisiertes Modell:

```text
same_input
+ same_initial_state
+ same_algorithm
+ same_operation_order
+ same_precision
+ deterministic_runtime
=> same_result
```

Unter diesen Annahmen wäre die notwendige Ergebnistoleranz theoretisch `0`.

Reales System:

```text
nominell gleiche Aufgabe
+ physikalische Betriebsbedingungen
+ Hardware-/Firmwarezustand
+ Treiber / Backend / Kernel
+ Parallelisierung
+ Floating-Point-Reihenfolge
+ Scheduler / Laufzeitumgebung
+ Mess- und Beobachtungsgrenzen
=> Ergebnis muss im Kontext bewertet werden
```

Daraus folgt nicht, dass physikalische Streuung direkt falsche Rechenergebnisse erzeugt. Sie gehört jedoch zum realen Kontext, in dem Garantien über Determinismus und Reproduzierbarkeit bewertet werden müssen.

## Konsequenz für L.I.A.R.A.

L.I.A.R.A. sollte Behauptungen nach Möglichkeit nicht ohne Kontext als absolute Aussagen speichern oder weiterreichen.

Statt:

> „Das System ist deterministisch.“

besser:

> „Das System ist unter den angegebenen Software-, Numerik- und Betriebsbedingungen deterministisch; außerhalb dieses Gültigkeitsbereichs ist die Aussage neu zu bewerten.“

Statt:

> „Gleiche Hardware liefert gleiche Ergebnisse.“

besser:

> „Baugleiche, spezifikationskonform arbeitende Hardware sollte bei identischem deterministischem Rechenpfad gleiche digitale Ergebnisse liefern; reale Trainingsreproduzierbarkeit kann dennoch durch Software-, Parallelitäts- und Numerikpfade abweichen.“

## Architekturidee

Epistemische Aussagen könnten perspektivisch nicht nur einen Wahrheits-/Confidence-Wert tragen, sondern einen expliziten Kontext:

```text
Claim
├── proposition
├── evidence[]
├── confidence
├── plausibility
├── assumptions[]
├── operating_conditions[]
├── validity_scope
├── uncertainty_sources[]
├── counter_evidence[]
└── provenance
```

Eine Bewertung wäre dann nicht nur:

```text
Claim -> true/false
```

sondern eher:

```text
Claim
+ Evidence
+ Assumptions
+ Environment
+ Constraints
-> Plausibility assessment
```

## Bezug zu L.I.A.R.A.s Wechselwirkungsprinzip

Die Beobachtung passt direkt zum bestehenden Denkmodell:

```text
Variablen
-> Relationen
-> Wechselwirkungen
-> Fluss
-> Systementwicklung
```

Eine Variable wie Temperatur ist für sich noch keine Erklärung. Erst ihre Relation zu Takt, Spannung, Fehlergrenzen, Kernelverhalten und Laufzeitbedingungen erzeugt eine relevante Wechselwirkung.

Plausibilität entsteht deshalb nicht aus einem Einzelwert, sondern aus der Konsistenz der Relationen im jeweiligen Kontext.

## Leitregel

> **Keine absolute Aussage ohne ihren Gültigkeitsbereich. Plausibilität ist eine Bewertung von Aussagen innerhalb konkreter Randbedingungen, nicht unabhängig von ihnen.**

Für L.I.A.R.A. bedeutet das: Nicht nur wissen, *was* als wahr gilt, sondern auch verstehen, *warum*, *unter welchen Bedingungen* und *mit welcher Unsicherheit* diese Aussage getragen wird.
