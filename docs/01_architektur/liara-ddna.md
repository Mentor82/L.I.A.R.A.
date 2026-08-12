# LIARA DDNA: Genome Cockpit und technische Architektur

Stand: 2026-08-09

Status: kanonisches Zielmodell

## Zweck

Dieses Dokument trennt die beiden Architekturprojektionen von LIARA:

- Das **Genome Cockpit** beschreibt LIARA als digitales System ueber seine
  stabile DDNA, Gene, Verantwortungen und Beziehungen.
- Die **Architecture Map** beschreibt die konkrete technische Umsetzung ueber
  Komponenten, Services, Fluesse, Reifegrade, Codepfade und Live-Evidenz.

Beide Sichten sind verbunden, aber ihre Knoten sind nicht identisch und muessen
nicht eins zu eins aufeinander abgebildet werden.

## Genome Cockpit

Das Genome Cockpit ist die identitaetsorientierte und funktionale Projektion.
Es beantwortet:

> Was gehoert dauerhaft zu LIARA und welche Rolle spielt es im Gesamtsystem?

Der aktuelle Cockpit-Stand besitzt:

- sechs Primary Genes als uebergeordnete Familien:
  `Orchestrator`, `Runtime`, `Memory`, `Governance`, `Evolution`, `Components`;
- zwoelf Gene im `genomeManifest`:
  `core`, `foundation`, `models`, `tools`, `security`, `orchestrator`, `worker`,
  `judge`, `validator`, `memory`, `observer`, `governance`.

Ein Cockpit-Gen beschreibt eine dauerhafte Systemfunktion, Verantwortung oder
Faehigkeit. Es ist weder ein Prozess noch ein Port. Deshalb koennen mehrere Gene
durch dieselbe technische Komponente ausgedrueckt werden.

## Architecture Map

Die Architecture Map ist die technische Projektion. Sie beantwortet:

> Wodurch und mit welchem Reifegrad wird ein Teil von LIARA umgesetzt?

Sie zeigt konkrete Knoten wie API, Orchestrator, Inference, Memory, SYS,
Validator oder Self Observer. Jeder Knoten besitzt einen Reifegrad:

| Status | Bedeutung |
| --- | --- |
| Geplant | Idee oder Zielarchitektur ohne belastbaren aktiven Codepfad |
| Teilweise | Contract, Datenmodell oder Teilpfad vorhanden; Integration oder Nachweis unvollstaendig |
| Implementiert | aktiver Codepfad vorhanden und durch Test- oder Live-Evidenz belegt |

Die Map zeigt damit den Weg von der Idee bis zur Umsetzung. Das Zielbild darf
alle drei Stufen enthalten; die Filter `Nur implementiert` und `Ist + teilweise`
erzeugen engere Betriebssichten.

Ein neues Cockpit-Gen verlangt nicht automatisch einen neuen Map-Knoten. Ein
neuer Map-Knoten ist dann sinnvoll, wenn eine eigene technische Prozess-,
Verantwortungs-, Skalierungs- oder Governancegrenze entsteht.

## Beziehung beider Sichten

```text
Primary Gene
-> Cockpit Gene
-> Expression Binding
-> Architecture Node
-> Runtime Instance
```

Beispiel:

```text
Genome Cockpit
C-GENE: Components
|- models   (bestehend; Text/Inference)
|- vision   (neu)
|- hearing  (neu)
`- speech   (neu)
       |
       `-- expressed_by ----------------------.
                                                |
Architecture Map                               v
Inference -> OpenVINO-Service :8040
             |- text engine
             |- vision engine
             |- audio-understanding engine
             `- speech engine
                |- NPU: Adapter / Transformer / DVAE
                `- CPU: Vocos
```

Die Gene sind getrennt, obwohl die technische Expression denselben Service
nutzt. Ein spaeterer Modell- oder Servicewechsel veraendert das Expression
Binding, nicht automatisch die DDNA.

## Zielerweiterung 12 auf 15

Das Genome Cockpit wird auf Gen-Ebene erweitert:

| Neues Gen | Primaere Familie | Dauerhafte Bedeutung | Technische Expression heute |
| --- | --- | --- | --- |
| `vision` | `components` | visuelle Inhalte wahrnehmen und interpretieren | OpenVINO/MiniCPM-o oder austauschbares Vision-Modell |
| `hearing` | `components` | Audiosignale wahrnehmen und semantisch verstehen | Audio Understanding / ASR Engine |
| `speech` | `components` | Sprache als Audio ausdruecken | TTS / Speech Synthesis Engine |

Damit gilt:

```text
12 bestehende Cockpit-Gene
+ vision
+ hearing
+ speech
= 15 Cockpit-Gene
```

Die sechs Primary Genes bleiben bestehen. `TTS` ist die aktuelle technische
Methode des Gens `speech`, nicht dessen stabiler DDNA-Name.

## Vision Evidence Binding

Das Gen `vision` ist nicht mit einem Bildcontainer oder einem Modellnamen
identisch. Seine aktuelle Expression wird ueber einen kanonischen,
inhaltgebundenen Beobachtungspfad realisiert:

```text
DDNA vision
  -> VisionRequest
  -> MiniCPM-o/OpenVINO VLMPipeline
  -> VisionResponse
  -> VisionImageEvidence (SHA-256, MIME, Dimensionen)
  -> Validator
```

Bildbytes bleiben transient. Das dauerhafte Evidence Binding beschreibt,
welches konkrete Bild tatsaechlich dekodiert wurde, ohne dessen Base64-Inhalt
in Memory, Prompt oder Logs zu vervielfaeltigen. Technisches Device Placement
ist Expression-Eigenschaft; bei NPU-Auswahl ist nicht automatisch die gesamte
Vision-Vorstufe auf der NPU ausgefuehrt.

## Source of Truth

```text
DDNA-Definition
-> genomeManifest / Genome Cockpit
-> Expression Bindings
-> Architecture Map
-> Runtime- und Live-Evidenz
```

Nicht zulaessig ist, aus der aktuellen Zahl der Services oder Map-Knoten die
Zahl der Cockpit-Gene abzuleiten. Ebenso darf das Frontend nicht allein
bestimmen, welche DDNA LIARA besitzt.

## Voice Identity

Die DDNA beschreibt, wie LIARA klingen soll. Die kanonische Instanz liegt in
`config/ddna/liara-voice-identity.json` und wird durch den Contract
`VoiceIdentity` validiert. Sie enthaelt stabile Eigenschaften wie warm, ruhig,
sanft, artikuliert und unaufgeregt.

Technische Auspraegungen gehoeren nicht in die Voice Identity:

```text
DDNA VoiceIdentity
  -> SpeechPlan
  -> Expression Binding / speaker_profile
  -> TTS Engine
  -> PCM Frames
  -> Encoder und Transport
  -> AudioArtifact oder AudioStream
```

Codec, Container, Samplerate, Backend, Device Placement und Port sind
Runtime- beziehungsweise Expression-Eigenschaften. Das aktuelle Binding
`gentle-feminine-v1` ist daher nicht die Voice Identity selbst, sondern eine
technische Annaeherung an sie.

## Typisiertes Zielmodell

```text
PrimaryGene
GenomeGene
ExpressionBinding
ArchitectureNode
RuntimeInstance
```

```text
GenomeGene --belongs_to--> PrimaryGene
GenomeGene --expressed_by--> ArchitectureNode
ArchitectureNode --deployed_as--> RuntimeInstance
```

`ExpressionBinding` darf zusaetzlich den Implementierungsstand einer einzelnen
Gen-Expression tragen. Dadurch kann der Architecture-Knoten `Inference`
insgesamt implementiert sein, waehrend Vision, Hearing oder Speech dort noch
geplant beziehungsweise teilweise integriert sind.

## Darstellungsregeln

### Genome Cockpit

- zeigt die sechs Primary Genes und zukuenftig 15 Cockpit-Gene;
- zeigt stabile Funktionen, Faehigkeiten und DDNA-Beziehungen;
- zeigt technische Expression und Reifegrad nur als nachgeordnete Evidenz;
- behandelt Port, Queue, Modellversion und Device Placement nicht als Gen.

### Architecture Map

- zeigt technische Komponenten, Services und gerichtete Fluesse;
- zeigt `Implementiert`, `Teilweise` und `Geplant` vom Ziel bis zum IST;
- zeigt Expression Bindings in Details oder Relationen;
- erzeugt nicht fuer jedes Cockpit-Gen zwangsweise einen Serviceknoten.

### Runtime und Operations

- zeigen Health, Latenz, Queue, Modellstand und Device Placement;
- belegen den aktuellen Zustand einer technischen Expression;
- veraendern die DDNA nicht automatisch.

## Umsetzungsplan

### P0: Cockpit-Katalog erweitern

- `vision`, `hearing` und `speech` mit stabilen IDs und Beziehungen als Gene
  13 bis 15 in das DDNA-Modell aufnehmen;
- Zuordnung zu `C-GENE: Components` bestaetigen;
- `models` als bestehende Text-/Inference-Verantwortung klar benennen.

### P1: Expression Bindings typisieren

- Gene und technische Architecture-Knoten getrennt modellieren;
- mehrere Gene an denselben Inference-/OpenVINO-Knoten binden koennen;
- pro Binding `planned`, `partial` oder `implemented` fuehren.

### P2: Genome Cockpit darstellen

- Helix von 12 auf 15 Gene erweitern;
- Gen-Reife und Runtime-Health nicht miteinander vermischen;
- technische Expression im Inspector nachgeordnet anzeigen.

### P3: Architecture Map ergaenzen

- beim bestehenden Inference-Knoten die gebundenen Gene und deren jeweiligen
  Expressionsstand anzeigen;
- nur bei einer echten neuen technischen Grenze einen weiteren Map-Knoten
  anlegen;
- Zielbild inklusive geplanter Elemente als Standardsicht behalten.

### P4: Live-Evidenz anbinden

- Capability-Health aus den Service-Contracts lesen;
- CPU/NPU-Platzierung, Queue und Modellversion in Runtime/Operations anzeigen;
- Statuswechsel nur mit nachvollziehbarer Test- oder Live-Evidenz vornehmen.
