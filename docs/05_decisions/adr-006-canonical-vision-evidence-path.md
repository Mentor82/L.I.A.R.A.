# ADR-006: Kanonischer Vision-Evidence-Pfad

Status: angenommen, kontrollierter Pilot live bestaetigt

Datum: 2026-08-11

## Kontext

MiniCPM-o 2.6 INT4 wurde auf Port 8040 bereits als OpenVINO
`VLMPipeline` geladen, jedoch nur mit Text aufgerufen. Bildteile aus der
OpenAI-Bridge verloren ihre Bytes; Web-UI und API behandelten Bilder wie
Textanhaenge oder Prompt-Platzhalter. Damit existierte keine beweisbare Kette
zwischen einem konkreten Bild und Liaras Antwort.

## Entscheidung

Vision wird als eigenstaendige DDNA-Faehigkeit mit gemeinsamer technischer
Expression im OpenVINO-Service umgesetzt:

```text
Web-UI Upload / OpenAI Data-URI
  -> ChatAttachment (transient image payload)
  -> API-Normalisierung + Malware-Scan + Sandbox-Grenze
  -> VisionRequest / VisionImageInput
  -> POST :8040/vision/analyze
  -> MiniCPM-o VLMPipeline(images=[OpenVINO Tensor])
  -> VisionResponse + SHA-256-gebundene ImageEvidence
  -> Orchestrator tool_results.vision
  -> Main-Inferenz
  -> Vision-Evidence-Validator
```

Remote Bild-URLs werden an dieser Grenze nicht abgerufen. Das verhindert
einen impliziten SSRF-/Netzwerkpfad. Eine spaetere URL-Aufloesung muss als
eigener policy-gepruefter Fetch mit MIME-, Groessen- und Inhaltspruefung
implementiert werden.

Base64-Bilddaten sind nur transient. Sie werden weder in Prompt noch History,
Tool-Evidence oder Logs uebernommen. Persistierte beziehungsweise sichtbare
Evidence enthaelt Bild-ID, erkannten MIME-Typ, Dimensionen und SHA-256.

## Contracts

- `VisionImageInput`: Bild-ID, MIME-Typ, Base64-Nutzlast, SHA-256, Dimensionen
- `VisionRequest`: Request-ID, Aufgabe, Prompt, bis zu vier Bilder, Tokenbudget
- `VisionImageEvidence`: tatsaechlich dekodierte Bildidentitaet und Dimensionen
- `VisionResponse`: Status, Beobachtung, Provider/Modell/Device, Evidence, Timing

## Device Placement

`OPENVINO_NPU_DEVICE=NPU` bleibt die Servicekonfiguration. Nach dem offiziellen
OpenVINO-GenAI-Verhalten laeuft bei einer VLM-Pipeline mit NPU-Auswahl jedoch
nur das Sprachmodell sicher auf der NPU. Vision-Frontend und Resampler duerfen
intern anders platziert werden. Health und Dokumentation behaupten daher keine
vollstaendige NPU-Ausfuehrung der gesamten Bildkette.

## Assurance

Ein `vision`-Tooloutput erdet nur, wenn Status `success`, `evidence=true` und
mindestens eine ImageEvidence vorliegen. Der Validator blockiert Behauptungen
wie „Auf dem Bild …“, wenn die Vision-Ausfuehrung fehlgeschlagen ist.

## Offene Gates

- End-to-End-Latenz reduzieren (gemessener Bridge-Live-Smoke: 187,9 s)
- Bridge-Implementierungen konsolidieren
- Abbruch/Backpressure fuer lange Vision-Inferenz explizit propagieren
- Mehrbild-, OCR- und Aufloesungsqualitaet systematisch evaluieren
- Policy-geprueften Remote-Image-Fetch nur bei nachgewiesenem Bedarf ergaenzen
