# ADR-005: Inferenzgesteuerte Web-Recherche ohne Quellen-Schlagwortliste

Stand: 2026-08-11  
Status: angenommen und implementiert

## Kontext

LIARA soll externe Informationsbeduerfnisse auch dann erkennen, wenn weder eine
URL noch ein zuvor konfigurierter Quellenname in der Anfrage steht. Eine feste
Liste aus Domaenen- oder Produkt-Schlagwoertern wuerde neue Dienste nicht
abdecken, Mehrdeutigkeiten verdecken und Routingwissen mit Berechtigungen
vermischen.

Gleichzeitig duerfen Modellwissen, Suchtreffer und ausgefuehrte Primaerabrufe
nicht als dieselbe Evidenz behandelt werden. Insbesondere ist ein
Suchmaschinen-Snippet kein Nachweis fuer die dort behauptete Fachinformation.

## Entscheidung

1. Vor dem Routing zerlegt eine Inferenz die Anfrage in einen typisierten
   `RetrievalIntent`: Informationsziel, vermutete Quelle, Entitaeten,
   Unsicherheiten, optionale Kandidaten-URL, Suchanfrage und Confidence.
2. Es gibt keine fachliche Schlagwort- oder Quellenliste. Der Router konsumiert
   den typisierten Intent nur bei gueltiger Inferenz und ausreichender
   Confidence.
3. Eine plausible, sichere URL kann direkt in den Abrufpfad gehen. Fehlt sie
   oder besteht Unsicherheit, wird genau eine begrenzte Suchseitenabfrage
   ausgefuehrt. Aktueller technischer Provider ist Bing RSS.
4. Die Suchergebnisse tragen `evidence_scope=discovery`. Sie sind Kandidaten,
   keine Grounding-Evidenz.
5. Eine zweite Inferenz bewertet die Kandidaten gegen Ziel, Entitaeten,
   Quellenhinweis und Unsicherheiten. Bei zu geringer Confidence greift eine
   deterministische semantische Rangfolge.
6. Der ausgewaehlte Primaerabruf durchlaeuft erneut die exakten
   Sicherheitsgrenzen: URL-Validierung, W/G/B-Policy, Pre-Action-Judge,
   optionale Governance und SYS-Audit. Die Suchfreigabe autorisiert niemals
   automatisch das Ziel.
7. Nur ein erfolgreicher Primaerabruf darf externe Fakten erden. Schlaegt er
   fehl, bleibt die Antwort ehrlich ohne erfundenes Resultat.

## Ablauf

```text
Nachricht
-> RetrievalIntent-Inferenz
   +-> keine externe Information -> normaler Chatpfad
   +-> sichere konkrete URL -> exakter Policy-/Judge-Abruf
   \-> unsicher -> begrenzte Suchseite
       -> Kandidaten (discovery, keine Evidenz)
       -> Kandidatenbewertung durch Inferenz
       -> ausgewaehlte URL
       -> frischer Policy-/Judge-/Governance-/Audit-Abruf
       -> erfolgreicher Primaeroutput als Evidenz
-> Validator + Post-Result-Judge
-> belegte Antwort oder ehrlicher Fehler
```

## Konsequenzen

- Neue Quellen koennen semantisch erkannt werden, ohne Codeaenderung fuer ein
  neues Schlagwort.
- Inferenz entscheidet ueber Bedeutung und Kandidaten, nicht ueber Rechte.
- Der Search-Provider ist austauschbare Infrastruktur, keine fachliche
  Wissensregistry.
- Die zusaetzlichen Inferenzstufen erhoehen derzeit die Latenz. Der erste
  erfolgreiche Live-Canary benoetigte rund 129 Sekunden.
- MiniCPM-o 2.6 INT4 ist als `VLMPipeline` auf der NPU technisch aktiv. Der
  direkte strukturierte Intentpfad benoetigte warm rund 19 Sekunden, war in
  wiederholten identischen Laeufen aber noch nicht stabil: Quellen,
  Identifikatoren oder Pflichtfelder drifteten. Deshalb bleibt
  `RETRIEVAL_INTENT_PROVIDER=ll_ol_fallback`; MiniCPM bleibt der reale
  NPU-Helper fuer begrenzte Helper-Aufgaben und ein opt-in Retrieval-Kandidat.
- Der Helper-Adapter verwendet fuer Retrieval-Strukturaufgaben den direkten
  `/infer`-Contract statt den Prompt nochmals in `source_text` einzubetten.
  Ein konfigurierter Main-Provider-Fallback verhindert, dass ein einzelner
  Helper-Contractfehler faelschlich als `kein externer Bedarf` gilt.

## Evidenz

- `services/orchestrator/retrieval_intent.py`: typisierte semantische Analyse
  und Kandidatenbewertung.
- `services/orchestrator/input_profiler.py` und `router.py`: Integration vor
  dem Toolrouting ohne Quellen-Schlagwortliste.
- `services/orchestrator/executor.py`: begrenzte Bing-RSS-Discovery und
  strukturierte Kandidaten.
- `services/orchestrator/orchestrator.py`: genau ein nachgelagerter,
  eigenstaendig gepruefter Primaerabruf.
- `services/orchestrator/validator.py`: Discovery-Snippets gelten nicht als
  erfolgreiche Tool-Evidenz.
- 338 fokussierte Unit- und Integrationstests bestanden am 2026-08-11.
- URL-Ausfuehrungsclaims sind nun an das konkrete `url_fetch.url` gebunden;
  ein erfolgreicher Homepage-Abruf darf keine behaupteten API-Abrufe decken.
- Live-Canary `4bede885-9ec3-43f6-8a06-15ceda0e6479`: freie Anfrage ohne URL,
  Scryfall semantisch erkannt, 8 Suchkandidaten bewertet,
  `https://api.scryfall.com/cards/BRO/118?lang=de` separat geprueft und
  erfolgreich abgerufen; Antwort `Lehm-Wiedergänger / Clay Revenant`,
  Validator `accept`, Post-Result-Judge `allow`, Confidence 0.97.
