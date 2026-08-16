export type ArchitectureStatus = "implemented" | "partial" | "prepared" | "planned" | "retired";
export type ArchitectureView = "system" | "chat" | "workspace" | "control";
export type ReadinessLevel = "development" | "controlled-pilot" | "staging-ready" | "production-capable";

export type ArchitectureNode = {
  id: string;
  title: string;
  subtitle: string;
  layer: string;
  status: ArchitectureStatus;
  description: string;
  responsibilities: string[];
  paths: string[];
  views: ArchitectureView[];
  x: number;
  y: number;
};

export type ArchitectureEdge = {
  from: string;
  to: string;
  label: string;
  kind: "data" | "decision" | "mutation" | "validation";
  views: ArchitectureView[];
};

export type ReadinessClaim = {
  level: ReadinessLevel;
  scope: string;
  environment: string;
  verifiedAt: string;
  evidence: string[];
  openGates: string[];
};

export type ArchitectureEvidence = {
  label: string;
  result: string;
  source: string;
  verifiedAt: string;
};

export type TracePreset = {
  id: string;
  label: string;
  description: string;
  view: ArchitectureView;
  nodes: string[];
  edges: Array<[string, string]>;
};

export const architectureViews: Array<{
  id: ArchitectureView;
  label: string;
  description: string;
}> = [
  { id: "system", label: "System", description: "Services, Grenzen und zentrale Beziehungen" },
  { id: "chat", label: "Chat-Fluss", description: "Vom Eingangskontext bis zur geprüften Antwort" },
  { id: "workspace", label: "Workspace-Fluss", description: "Planen, mutieren, verifizieren und testen in WSL" },
  { id: "control", label: "Kontrollkreis", description: "Beobachtung, Bewertung, Governance und Rückkopplung" },
];

export const architectureNodes: ArchitectureNode[] = [
  {
    id: "clients", title: "Zugänge", subtitle: "TUI · Web · CLI · OpenAI API", layer: "Interface", status: "implemented",
    description: "Mehrere Zugaenge verwenden dieselben API-Contracts. Das Webfrontend reicht lokale und entfernte Browseraufrufe ueber einen Same-Origin-BFF nach der weiterhin loopback-gebundenen LIARA-API durch. Der Web-Chat bindet Antwort, Streaming-Fortschritt, Memory-Kontext, Validator-Entscheidung, Reasoning-Metriken, Tool-Aktivitaet, Artefakte und TTS an denselben sichtbaren Run-Nachweis.",
    responsibilities: ["Anfragen, Textdateien und Bilder erfassen", "Dateien vor dem Chat über die kontrollierte Upload-Grenze persistieren und scannen", "Sessions fuehren", "Remote-Browser sicher ueber :3001/api/liara anbinden", "Antworten, Artefakte und TTS darstellen", "Run-Evidenz und Assurance nachvollziehbar projizieren", "Dreaming-, Assurance- und Quality-Zustand beobachten"],
    paths: ["services/tui", "frontend/web-ui/src/app/page.tsx", "frontend/web-ui/src/app/internal-shell.css", "services/cli"], views: ["system", "chat", "control"], x: 70, y: 235,
  },
  {
    id: "openai-bridge", title: "OpenAI Bridge", subtitle: "Continue / VS Code / OpenAI-Format", layer: "Boundary", status: "partial",
    description: "Uebersetzt OpenAI-kompatible Client-Aufrufe in LIARAs kanonischen API-Contract. Der Continue-Pfad erhält Bildbytes aus image_url und input_image; Remote-URLs bleiben bewusst unaufgelöst. Zwei Implementierungen existieren weiterhin.",
    responsibilities: ["OpenAI-Requests normalisieren", "Inline-Bilder ohne Logging der Nutzlast übernehmen", "Streaming-Formate adaptieren", "Traceability in den LIARA-Request uebernehmen"],
    paths: ["services/openai_bridge/bridge_service.py", "scripts/continue_openai_bridge.py"], views: ["system", "chat"], x: 70, y: 355,
  },
  {
    id: "api", title: "LIARA API", subtitle: "FastAPI · Contracts · Streaming", layer: "Boundary", status: "implemented",
    description: "Kanonischer Eintrittspunkt. Die API normalisiert und scannt Anhänge an der Vertrauensgrenze, transportiert Aufträge und enthält keine eigene Modelllogik.",
    responsibilities: ["Contracts validieren", "Bildbytes auf Sandbox, MIME, Größe, Pixelzahl und Malware prüfen", "Request-/Run-IDs propagieren", "Streams und Artefakte ausgeben"],
    paths: ["services/api/app.py", "services/api/routers", "services/api/models.py"], views: ["system", "chat", "workspace"], x: 250, y: 235,
  },
  {
    id: "profile", title: "Situation Profile", subtitle: "Kontext · Domäne · Mood · Handlungsbedarf", layer: "Perception", status: "implemented",
    description: "Erstellt aus der eingehenden Nachricht ein typisiertes Profil für Analysieren, Denken, Antworten, Planen und Handeln. Eine vorgeschaltete Inferenz zerlegt externen Informationsbedarf ohne fachliche Quellen-Schlagwortliste.",
    responsibilities: ["Eingangskontext klassifizieren", "Fachdomäne und Mood ableiten", "RetrievalIntent aus Ziel, Entitäten, Quelle und Unsicherheiten bilden", "Planungsbedarf signalisieren"],
    paths: ["services/orchestrator/input_profiler.py", "services/orchestrator/retrieval_intent.py"], views: ["chat", "control"], x: 430, y: 115,
  },
  {
    id: "orchestrator", title: "Orchestrator", subtitle: "Routing · Planung · Ablaufsteuerung", layer: "Coordination", status: "implemented",
    description: "Verbindet die spezialisierten Pfade, ohne deren Verantwortlichkeiten zu übernehmen. Web-Recherche trennt semantische Discovery strikt vom separat geprüften Primärabruf.",
    responsibilities: ["Route wählen", "Pläne koordinieren", "Unsichere RetrievalIntents über begrenzte Suchseiten-Discovery verfeinern", "Suchkandidaten als nicht-erdende Discovery behandeln", "Konkretes Tool-Payload an Judge und Executor binden", "Antwortsynthese klar als Phase nach Toolausführung kennzeichnen", "Zustand und Trace durchreichen", "Explizite tools_override-Selektion respektieren"],
    paths: ["services/orchestrator/orchestrator.py", "services/orchestrator/reasoning_control.py", "services/orchestrator/librarian_pipeline.py", "services/orchestrator/tool_discovery.py", "services/orchestrator/generation_pipeline.py"], views: ["system", "chat", "workspace", "control"], x: 430, y: 235,
  },
  {
    id: "vision", title: "Vision Evidence", subtitle: "MiniCPM-o INT4 · OpenVINO VLM · :8040", layer: "Perception", status: "partial",
    description: "Kanonischer Bildpfad mit echten OpenVINO-Tensoren. Jede erfolgreiche Beobachtung ist an SHA-256, MIME-Typ und Dimensionen der dekodierten Bilder gebunden; Bildbytes bleiben transient. Vision teilt Port 8040 mit Inference, bleibt aber ein eigenes DDNA-Gen.",
    responsibilities: ["Bis zu vier Bilder normalisieren", "VLMPipeline mit images=[Tensor] ausführen", "Beobachtung und ImageEvidence erzeugen", "Fehlschläge als evidence=false markieren", "Visuelle Behauptungen im Validator absichern"],
    paths: ["services/contracts/vision.py", "services/vision", "services/inference/providers/openvino.py", "docs/05_decisions/adr-006-canonical-vision-evidence-path.md"], views: ["system", "chat", "control"], x: 610, y: 475,
  },
  {
    id: "planner", title: "Planner", subtitle: "Typisierte Schritte · Budgets", layer: "Reasoning", status: "implemented",
    description: "Zerlegt komplexe Aufträge in einzeln ausführbare, überprüfbare Schritte.",
    responsibilities: ["Schritttypen erzeugen", "Abhängigkeiten ordnen", "Kosten- und Tiefengrenzen beachten"],
    paths: ["services/orchestrator/planner.py", "services/orchestrator/workspace_planner.py"], views: ["chat", "workspace", "control"], x: 610, y: 115,
  },
  {
    id: "context", title: "Context & Evidence", subtitle: "Memory · Graph · Retrieval", layer: "Knowledge", status: "partial",
    description: "Verdichtet relevanten Kontext und Evidenz. Speichert und verwaltet Sessions, Staging, Facts und Graph-Beziehungen über das modularisierte Memory-Subpackage.",
    responsibilities: ["Kontext transformieren", "Fakten und Relationen abrufen", "Evidenz gewichten"],
    paths: ["services/orchestrator/context_controller.py", "services/memory/stores", "services/memory/store.py", "services/memory/tier_store.py"], views: ["system", "chat", "control"], x: 610, y: 355,
  },
  {
    id: "embedding", title: "Embedding Worker", subtitle: "C++ / OpenVINO / NPU / LiNeP", layer: "Infrastructure", status: "implemented",
    description: "Nativer C++/OpenVINO-Service fuer Embeddings auf Port 8030. Er laeuft primaer auf der NPU und meldet seinen Worker-Slot ueber LiNeP; Python bleibt nur Fallback/Wrapper.",
    responsibilities: ["Texte in 1024-dimensionale Vektoren umwandeln", "Semantisches Retrieval versorgen", "LiNeP-Slot und Heartbeat bereitstellen"],
    paths: ["src/emeddingserver", "workers/embedding/exec", "services/embedding"], views: ["system", "chat"], x: 790, y: 355,
  },
  {
    id: "inference", title: "Inference", subtitle: "Provider · Modelle · Text, Vision & Speech", layer: "Execution", status: "implemented",
    description: "Austauschbarer technischer Ausdruck für mehrere Cockpit-Gene. MiniCPM-o 2.6 INT4 läuft als OpenVINO-VLMPipeline; Text, Vision und Speech teilen die Servicegrenze, behalten aber getrennte Engines und Contracts. Bei NPU-Auswahl ist nur das Sprachmodell sicher NPU-platziert.",
    responsibilities: ["Provider auswählen", "MiniCPM-INT4-Helper ausführen", "VisionRequest mit echten Bildtensoren ausführen", "Prompt/Context ausführen", "Speech als PCM16-WAV oder Opus-/PCM-Stream erzeugen", "Cockpit-Gene an technische Expressionen koppeln", "Nutzungsmetriken liefern"],
    paths: ["services/inference", "services/inference/minicpmo_tts", "docs/01_architektur/liara-ddna.md", "docs/05_decisions/adr-003-openvino-audio-tts-service.md"], views: ["system", "chat"], x: 790, y: 115,
  },
  {
    id: "tools", title: "Tools / SYS", subtitle: "Policy-gated · deterministisch", layer: "Execution", status: "implemented",
    description: "Kanonischer Ausführungspfad in Liaras Debian-Arbeitsraum. W/G/B-Profile begrenzen Kommandos und Argumente; kontextuell validierte read-only HTTP(S)-Abrufe laufen auditierbar, Blacklist-Treffer sind nicht approvable. Judge und Executor verwenden dasselbe vorbereitete Payload; Fehlschläge bleiben sichtbar, tragen aber evidence=false.",
    responsibilities: ["Whitelist direkt und Greylist kontextuell prüfen", "Blacklist endgültig abweisen", "Befehle in Debian WSL ausführen", "Julia-Compute nach WSL routen", "Erfolg und nicht-erdende Failure-Envelopes strukturiert zurückgeben"],
    paths: ["services/tools", "services/sys", "services/simulation/bridge.py"], views: ["system", "chat", "workspace", "control"], x: 790, y: 235,
  },
  {
    id: "wsl", title: "WSL Workspace", subtitle: "Debian · Sessions · .venv · Julia", layer: "Sandbox", status: "implemented",
    description: "Realer Arbeits- und Simulationsraum innerhalb von WSL. Native Sessions kapseln Snapshot, Worktree, Artefakte und kontrollierte Zerstoerung; Mutation unterliegt zentraler Governance.",
    responsibilities: ["Session-Snapshots isolieren", "Dateien nach Approval mutieren", "Compute ausführen", "Read-after-write verifizieren", "Testsession zerstoeren"],
    paths: ["/home/liara/workspace", "services/simulation/wsl_session_runtime.py", "services/tools/builtin/wsl_session.py"], views: ["workspace", "control"], x: 610, y: 235,
  },
  {
    id: "verify", title: "Mutation Verification", subtitle: "Read-back · Stat · Hash · Diff", layer: "Assurance", status: "implemented",
    description: "Eine Mutation gilt erst als erfolgt, wenn der beobachtete Zustand sie bestätigt.",
    responsibilities: ["Nach Schreibvorgängen erneut lesen", "Hashes und Metadaten vergleichen", "Timeouts und Abweichungen melden"],
    paths: ["services/tools"], views: ["workspace", "control"], x: 790, y: 355,
  },
  {
    id: "judge", title: "Validator & Judge", subtitle: "Pre-Action Gate · Post-Result", layer: "Assurance", status: "implemented",
    description: "Zwei Phasen, eine Instanz: ein fail-closed Pre-Action-Gate vor jedem Tool-Dispatch (Profile fuer alle 6 aktuell registrierten Tools -- sys, compute.run, compute.generate, orientation, plot_chart, wsl_session; Multi-Tool-Turns werden pro Tool-Name mit segmentierten Parametern bewertet, most-restrictive-wins; unbekannte Aktionen werden ohne Profil grundsaetzlich blockiert) sowie die bestehende Post-Result-Pruefung von Ausgabefaehigkeit und Qualitaet, logisch getrennt vom erzeugenden Pfad.",
    responsibilities: ["Pre-Action-Dispatch fail-closed durchsetzen, bevor ein Tool ausgefuehrt wird", "Multi-Tool-Turns pro Tool-Name mit segmentierten Parametern bewerten (most-restrictive-wins)", "accept/revise/warn/block", "Post-Result-Judge und Validator mit denselben Tooloutputs versorgen", "Tool-Evidence-Integrität erzwingen", "Plausibilität bewerten", "Reward- und Confidence-Signale liefern"],
    paths: ["services/judge", "services/orchestrator/validator.py", "services/orchestrator/tool_discovery.py"], views: ["system", "chat", "control"], x: 970, y: 115,
  },
  {
    id: "ai-validator", title: "ai-validator", subtitle: "Externer Validierungs-Worker", layer: "Assurance", status: "implemented",
    description: "Fuehrt reale Code- und Workspace-Pruefungen in einer austauschbaren isolierten Laufzeit aus und traegt bei Dreaming-Pruefungen eine unveraenderliche Proposal-Bindung.",
    responsibilities: ["Jobs annehmen", "Tests und Regeln ausfuehren", "Findings und Artefakte zurueckgeben", "ValidatorJobSubject durchreichen"],
    paths: ["services/workers/ai_validator"], views: ["system", "workspace", "control"], x: 970, y: 235,
  },
  {
    id: "memory", title: "Memory & Graph", subtitle: "History · Fakten · Relationen", layer: "Persistence", status: "implemented",
    description: "Persistiert nachvollziehbare Zustaende, Fakten, Beziehungen und Dreaming-Proposals. Optional erzeugte Complexity-/Coverage-Signale bleiben reine Validator-Evidenz; Retention ist preview-first.",
    responsibilities: ["History speichern", "Graph-Zustaende verwalten", "Lifecycle und Provenienz erhalten", "Staging/Touch/Proposal-Signale fuehren", "Session-History als Summary-Proposal bereitstellen", "Akzeptierte Graph-Kanten read-only als Evidenz liefern", "Deterministische Proposal-Qualitaetssignale bereitstellen", "Retention begrenzt und auditierbar anwenden"],
    paths: ["services/memory", "services/contracts/memory_dreaming.py"], views: ["system", "chat", "control"], x: 970, y: 355,
  },
  {
    id: "governance", title: "Governance", subtitle: "Proposal · Freigabe · Rollback", layer: "Control", status: "partial",
    description: "Trennt Analyse und Simulation von produktiver Veraenderung. Zentrales SYS-Enforcement gilt fuer API, Orchestrator und Workspace-Agent; allgemeines Apply ist explizit, vorhandene Workspace-Dateien besitzen einen begrenzten kompensierenden Rollback.",
    responsibilities: ["Berechtigung zentral bestimmen", "Policy-blockierte Aktionen abweisen", "Action-Digest und Invocation-Limit erzwingen", "governance_required an Aufrufer zurueckgeben", "Apply-Zustand persistieren", "Rollback-Evidenz und Kompensation pruefen"],
    paths: ["services/api/routers/governance.py", "services/tools/governance.py", "services/api/services/governance_service.py", "docs/HYBRID_CONTROL_SYSTEM.md", "docs/09_reference/SYS_AUDIT.md"], views: ["system", "control"], x: 790, y: 475,
  },
  {
    id: "simulation", title: "Simulation / Dreaming", subtitle: "Varianten · Utility · Risiko", layer: "Evolution", status: "partial",
    description: "Erzeugt und bewertet moegliche Zustaende. Deterministische Modelle laufen ueber die allowlistete WSL-Julia-Bridge; Memory-Dreaming fuehrt Staging, Summary, direkte Graph-Evidenz und optionale Complexity-/Coverage-Signale zu Proposals.",
    responsibilities: ["Hypothesen erzeugen", "Varianten isoliert pruefen", "Staging-, Summary-, Relations-, Quality- und Validator-Evidenz verdichten", "Proposal statt Produktivmutation erzeugen", "Graph-No-Speculation erhalten"],
    paths: ["services/simulation", "services/memory/app.py", "docs/02_services/liara-dreaming.md"], views: ["system", "control"], x: 70, y: 475,
  },
  {
    id: "heartbeat", title: "Resource Heartbeat", subtitle: "State Curve · LiNeP · Scheduler", layer: "Operations", status: "partial",
    description: "Eigenstaendige Wahrnehmungsinstanz fuer standardisierte Ressourcenmessungen und zeitliche Zustandskurven. LiNeP- und Scheduler-Konsum sind noch offen.",
    responsibilities: ["Quellen neutral normalisieren", "CPU/RAM/GPU/NPU sowie Temperatur und Energie zeitlich abbilden", "Scheduler-Evidenz ohne eigene Ausfuehrungsrechte liefern"],
    paths: ["services/heartbeat", "services/contracts/heartbeat.py", "docs/02_services/liara-heartbeat.md"], views: ["system", "control"], x: 430, y: 475,
  },
  {
    id: "self-observer", title: "Self Observer", subtitle: "Software · Hardware · zyklischer Zustand", layer: "Perception", status: "partial",
    description: "Eigenstaendige zyklische Selbstbeobachtung auf Port 8060. Der Observer bleibt read-only; ein getrenntes Assurance-Gate bildet kontrollierte, standardmaessig nur beobachtete ai-validator-Entscheidungen.",
    responsibilities: ["Software- und Hardwarezustand zyklisch beobachten", "Herkunft, Frische und Confidence erhalten", "Validator-Pruefbereitschaft mit Hysterese und Mindestabstand begrenzen"],
    paths: ["services/self_observer", "services/contracts/self_observer.py", "docs/02_services/liara-self-observer.md"], views: ["system", "control"], x: 250, y: 475,
  },
  {
    id: "evidence-engine", title: "Evidence Engine", subtitle: "EvidenceState · Target-Identitaet", layer: "Assurance", status: "implemented",
    description: "Klassifiziert Tool-Outputs in einen EvidenceState (FOUND, NOT_FOUND_IN_SEARCH, UNRESOLVED, CONNECTOR_UNAVAILABLE, ACCESS_DENIED, PRIVATE_CONFIRMED, DOES_NOT_EXIST_CONFIRMED, CONFLICTING_EVIDENCE) und erzwingt damit NO_RESULT != NOT_EXISTS sowie UNKNOWN != FALSE. Optional traegt eine Beobachtung eine kanonische EvidenceTarget-Identitaet (namespace + canonical_ref, beide zwingend explizit vom Connector geliefert, nie eines aus dem anderen geraten) statt reinem Freitext-Ziel; Aequivalenz zwischen zwei Identifikatoren entsteht ausschliesslich ueber explizit deklarierte aliases, niemals ueber Fuzzy-Matching und niemals ueber display_name (reiner Praesentationstext ohne Bindungsautoritaet). Der Validator bindet einen Claim zweistufig: zuerst wird zustandsunabhaengig per Volltext-Identifier-Abgleich ermittelt, welches kanonische Ziel gemeint ist (Mehrdeutigkeit blockiert), erst danach wird genau dieses Ziel auf den geforderten State geprueft, ohne Fallback auf ein anderes Ziel. Diese Ansicht vereinfacht auf die Tool-Output-Kante; die Engine klassifiziert auf jedem Lauf zusaetzlich context_channels und source_counts.",
    responsibilities: [
      "Tool-Outputs in EvidenceState klassifizieren",
      "EvidenceTarget nur aus explizit geliefertem canonical_ref + canonical_namespace bilden",
      "Validator-Claims per Volltext-Identifier an genau ein aufgeloestes Ziel binden, Mehrdeutigkeit fail-closed behandeln",
      "Fuer Ziele ohne kanonische Identitaet auf die schwaechere Text-Heuristik zurueckfallen",
      "Schwache/negative Evidenz nie stillschweigend zu einer staerkeren Behauptung aufwerten lassen",
    ],
    paths: ["services/contracts/evidence_state.py", "services/orchestrator/evidence_engine.py", "services/orchestrator/validator.py"],
    views: ["system", "chat", "control"], x: 970, y: 475,
  },
  {
    id: "multimodal-track", title: "Multimodal Track", subtitle: "Vorbereitet · nicht verdrahtet", layer: "Perception", status: "prepared",
    description: "OpenVINO-Qwen2.5-VL-Pipeline implementiert und per Ground-Zero-Inferenz verifiziert; nicht in den Orchestrator eingebunden (keine Referenz ausserhalb der eigenen Module, services/vision/__init__.py exportiert die Klassen nicht). Kokoro-TTS ist eine deutlich unreifere Nebenspur: synthesize_segment() erzeugt aktuell einen reinen Sinuston, es wurde nie ein echtes Modell exportiert. Der reale, live TTS-Pfad bleibt services/inference/tts_adapter.py (bereits an anderer Stelle korrekt abgebildet). Dieser Knoten ist bewusst mit keinem anderen Knoten in diesem Diagramm verbunden -- er existiert im Repository, ist aber aus keinem aktiven Fluss erreichbar.",
    responsibilities: [
      "Qwen2.5-VL-Vision-Encoder auf echten OpenVINO-Tensoren ausfuehren (isoliert, nicht orchestriert)",
      "Visuelle Perception-Buffer- und Assembly-Bausteine bereitstellen, ohne Aufrufer im Orchestrator",
      "Kokoro-Sprachsynthese als Platzhalter fuehren, noch ohne exportiertes Modell",
    ],
    paths: ["services/vision/openvino_vit_worker.py", "services/vision/perception_buffer.py", "services/orchestrator/multimodal_assembly.py", "services/contracts/multimodal.py", "services/speech/kokoro_worker.py"],
    views: ["system"], x: 250, y: 355,
  },
];

export const architectureEdges: ArchitectureEdge[] = [
  { from: "clients", to: "api", label: "Request / Stream", kind: "data", views: ["system", "chat", "control"] },
  { from: "openai-bridge", to: "api", label: "Formatadapter", kind: "data", views: ["system", "chat"] },
  { from: "api", to: "profile", label: "Eingang", kind: "data", views: ["chat"] },
  { from: "api", to: "orchestrator", label: "Contract", kind: "data", views: ["system", "workspace"] },
  { from: "orchestrator", to: "vision", label: "VisionRequest", kind: "decision", views: ["system", "chat"] },
  { from: "vision", to: "judge", label: "Image Evidence", kind: "validation", views: ["system", "chat", "control"] },
  { from: "profile", to: "orchestrator", label: "Situation", kind: "decision", views: ["chat", "control"] },
  { from: "orchestrator", to: "planner", label: "komplex?", kind: "decision", views: ["chat", "workspace", "control"] },
  { from: "orchestrator", to: "context", label: "Kontextbedarf", kind: "decision", views: ["chat"] },
  { from: "context", to: "inference", label: "Evidenz", kind: "data", views: ["chat"] },
  { from: "planner", to: "inference", label: "Plan/Prompt", kind: "data", views: ["chat"] },
  { from: "planner", to: "tools", label: "Schritt", kind: "decision", views: ["workspace", "control"] },
  { from: "orchestrator", to: "tools", label: "Toolcall", kind: "decision", views: ["system", "chat"] },
  { from: "tools", to: "judge", label: "Execution Evidence", kind: "data", views: ["system", "chat", "control"] },
  { from: "tools", to: "wsl", label: "ausführen", kind: "mutation", views: ["workspace", "control"] },
  { from: "wsl", to: "verify", label: "Zustand", kind: "validation", views: ["workspace", "control"] },
  { from: "verify", to: "planner", label: "nächster Schritt", kind: "validation", views: ["workspace", "control"] },
  { from: "wsl", to: "ai-validator", label: "Workspace", kind: "validation", views: ["workspace"] },
  { from: "inference", to: "judge", label: "Entwurf", kind: "validation", views: ["system", "chat"] },
  { from: "judge", to: "memory", label: "bewerteter Lauf", kind: "data", views: ["system", "chat", "control"] },
  { from: "embedding", to: "memory", label: "Vektoren", kind: "data", views: ["system", "chat"] },
  { from: "ai-validator", to: "memory", label: "Findings", kind: "data", views: ["system", "workspace", "control"] },
  { from: "memory", to: "context", label: "Recall", kind: "data", views: ["system", "chat", "control"] },
  { from: "memory", to: "simulation", label: "Finding", kind: "data", views: ["control"] },
  { from: "simulation", to: "governance", label: "Proposal", kind: "validation", views: ["system", "control"] },
  { from: "ai-validator", to: "governance", label: "Assurance-Report", kind: "validation", views: ["control"] },
  { from: "governance", to: "tools", label: "Freigabe", kind: "decision", views: ["control"] },
  { from: "heartbeat", to: "orchestrator", label: "Kapazitaet", kind: "data", views: ["system", "control"] },
  { from: "embedding", to: "heartbeat", label: "LiNeP-Slot", kind: "data", views: ["system"] },
  { from: "api", to: "self-observer", label: "Servicezustand", kind: "data", views: ["system", "control"] },
  { from: "heartbeat", to: "self-observer", label: "Hardwarezustand", kind: "data", views: ["system", "control"] },
  { from: "ai-validator", to: "self-observer", label: "Pruefevidenz", kind: "validation", views: ["system", "control"] },
  { from: "self-observer", to: "orchestrator", label: "Systemzustand", kind: "data", views: ["system", "control"] },
  { from: "orchestrator", to: "judge", label: "Pre-Action Gate", kind: "decision", views: ["system", "chat", "workspace", "control"] },
  { from: "tools", to: "evidence-engine", label: "Tool Output", kind: "data", views: ["system", "chat", "control"] },
  { from: "evidence-engine", to: "judge", label: "Evidence States", kind: "validation", views: ["system", "chat", "control"] },
];

export const readinessClaims: Partial<Record<string, ReadinessClaim>> = {
  "openai-bridge": {
    level: "development", scope: "OpenAI-kompatibler Formatadapter", environment: "Optionaler lokaler Dienst auf Port 8011",
    verifiedAt: "2026-08-08", evidence: ["Chat-Completions-, Responses- und Models-Endpoints implementiert", "Continue-Adapter besitzt Attachment- und Streaming-Pfad", "Live Health auf Port 8011 HTTP 200"],
    openGates: ["Zwei Implementierungen noch nicht konsolidiert"],
  },
  api: {
    level: "controlled-pilot", scope: "Lokale API plus Same-Origin-Web-BFF", environment: "Windows, vertrauenswuerdiger lokaler Host/LAN",
    verifiedAt: "2026-08-11", evidence: ["Health und Backend-Aggregat 6/6 healthy", "LAN-BFF HTTP 200", "SSE progressiv nach 329 ms", "WebM/Opus erster Chunk ueber BFF"],
    openGates: ["Backend-Aggregat weist noch keine Latenz pro Probe aus", "Keine durchgaengige Netzwerk-Authentisierung", "Provider-Konfiguration driftet"],
  },
  inference: {
    level: "controlled-pilot", scope: "Textinferenz, MiniCPM-o Vision und Speech", environment: "OpenVINO-Service :8040, kontrollierter API-Proxy :8010",
    verifiedAt: "2026-08-11", evidence: ["Gemeinsamer segmentweiser PCM16-Produzent", "WebM/Opus default, Ogg/Opus und PCM16 alternativ", "Progressives Browserplayback mit Cancellation", "46 finale fokussierte Tests und WAV-Kompatibilität bestanden"],
    openGates: ["Subjektives Hörqualitäts- und Browsermatrix-Gate offen", "LiNeP-Codec-Aushandlung fehlt", "Vision-Live-Smoke und Qualitätsmatrix stehen aus", "VLM-Vision-Frontend ist bei NPU-Auswahl nicht garantiert NPU-platziert"],
  },
  vision: {
    level: "controlled-pilot", scope: "Bild-Upload/Data-URI bis hashgebundene MiniCPM-o-Beobachtung", environment: "Web/Bridge -> API :8010 -> OpenVINO :8040",
    verifiedAt: "2026-08-11", evidence: ["Kanonische VisionRequest/VisionResponse-Contracts", "Echte images=[Tensor]-Ausführung", "Remote-URL Default-Deny", "Vision-Evidence-Validator", "80 fokussierte Backendtests und erfolgreicher Web-UI-Build", "OpenAI-Bridge-End-to-End-Smoke HTTP 200"],
    openGates: ["End-to-End-Latenz im Live-Smoke 188 s", "Abbruch langer Vision-Inferenz noch nicht durchgängig", "OCR/Mehrbild/Auflösungs-Evaluation offen"],
  },
  orchestrator: {
    level: "controlled-pilot", scope: "Lokaler Orchestrierungs-Kern", environment: "Lokale LIARA-Runtime",
    verifiedAt: "2026-08-16", evidence: ["Konkretes Judge-/Executor-Payload gebunden", "Failure-Envelopes mit evidence=false", "Inference-first Web-Discovery ohne Quellen-Schlagwortliste", "URL-Ausführungsclaims an konkrete Fetch-Evidenz gebunden", "338 fokussierte Tests bestanden"],
    openGates: ["Voller Unit-Lauf: 40 failed, 1521 passed, 5 skipped (Fehlschläge vorbestehend in Memory/Reasoning/Relations/Retrieval, keine Evidence-/Judge-/Validator-/Governance-Regression)", "relation_node_key-Signatur-Mismatch (Orchestrator._relation_node_key ruft die zweiargumentige Funktion einargumentig auf) separat als Issue erfasst, nicht hier gefixt", "Retrieval-Livepfad ist weiterhin langsam", "MiniCPM-INT4-RetrievalIntent bewahrt Quellen und Identifikatoren noch nicht wiederholbar", "Zwei Embedding-Query-Tests rufen eine Instanzmethode statisch auf", "Sieben NPU-Helper-/Retry-Tests erwarten die alte Provider-Aufrufreihenfolge", "Grosses Integrationsmodul", "Globale Ressourcensteuerung fehlt"],
  },
  wsl: {
    level: "controlled-pilot", scope: "Isolierte Session + risk-based Governance + verifizierte Mutation", environment: "Debian WSL unter Nutzer liara",
    verifiedAt: "2026-08-08", evidence: ["Realer isolierter Governance-Canary", "Mutation vor Approval blockiert", "Automatischer Pending-Handoff", "Plan-Resume ab step_index + 1", "Read-after-write verifiziert", "Replay blockiert", "Session zerstoert"],
    openGates: ["Noch keine temporaeren OS-Nutzer", "Validierte Kandidaten werden nicht automatisch in den Windows-Projektroot promoted"],
  },
  "ai-validator": {
    level: "production-capable", scope: "Submit → Execute → Result im geprueften lokalen Scope", environment: "Austauschbares isoliertes Backend, aktuell Docker Compose",
    verifiedAt: "2026-07-14", evidence: ["Reale Ausfuehrung Exit Code 0", "Self-Validator- und Integrationspfad bestaetigt"],
    openGates: ["Reports noch nicht vollstaendig als strukturierte Findings uebernommen"],
  },
  governance: {
    level: "controlled-pilot", scope: "Zentrales risk_based SYS-Enforcement mit gebundener Approval", environment: "Lokale Runtime",
    verifiedAt: "2026-08-15", evidence: ["Coordinator-Guard fuer API/Orchestrator/Workspace", "SHA-256 Action-Bindung", "Idempotenter Workspace-Handoff", "Explizites allgemeines Apply", "Hashgebundener Datei-Rollback", "Created/Decided/Attempted/Result Eventstream"],
    openGates: ["Rollback fuer neue Dateien, Append, Verzeichnisse, Installationen, Netzwerk und Code nicht verfuegbar", "Validierte Kandidaten werden noch nicht in den Windows-Projektroot promoted"],
  },
  heartbeat: {
    level: "controlled-pilot", scope: "Lokale Ressourcen-Wahrnehmungsinstanz", environment: "Windows, eigenstaendiger Dienst auf Port 8050",
    verifiedAt: "2026-08-08", evidence: ["Versionierter Observation-Contract", "Live healthy, Trend improving, Stabilitaet 0.88", "Default-deny externe Ingestion"],
    openGates: ["GPU/NPU/Temperaturquelle noch nicht konfiguriert", "Nicht an LiNeP und Scheduler angebunden", "Zeitreihe noch prozesslokal"],
  },
  "self-observer": {
    level: "controlled-pilot", scope: "Lokale Selbstbeobachtung und begrenztes Assurance-Gate", environment: "Windows, eigenstaendiger Dienst auf Port 8060",
    verifiedAt: "2026-08-08", evidence: ["Drei normalisierte Evidenzdomaenen", "Persistente StateEnvelope-Historie", "API-Proxy live success", "Separates 12s-Budget fuer Backend-Probe implementiert"],
    openGates: ["Observer-Neustart und Live-Verifikation des neuen Probe-Budgets stehen aus", "Validator-Evidenz vom 15.07. ist veraltet und failed", "Validator-quick ist auf dem Gesamtroot nicht ausreichend begrenzt", "Kein Orchestrator-/Dreaming-Konsum", "Realer zyklischer Submit bleibt explizit deaktiviert"],
  },
  embedding: {
    level: "staging-ready", scope: "Nativer Embedding-Hot-Path", environment: "Lokaler C++/OpenVINO-Service auf NPU",
    verifiedAt: "2026-08-08", evidence: ["Health auf Port 8030 healthy, Uptime ca. 86 Minuten", "Runtime openvino-cpp, NPU, 1024 Dimensionen", "LiNeP Worker 30 und TCP 8767 aktiv"],
    openGates: ["Aktueller Prozess hat noch keinen persistierten 100x-/Parallel-Lastbeleg", "Globale Scheduler-/Ressourcensteuerung noch nicht geschlossen", "Python-Fallback bleibt separater Degraded Path"],
  },
  tools: {
    level: "controlled-pilot", scope: "Registrierte Tool-Ausfuehrung hinter fail-closed Pre-Action-Judge-Gate plus W/G/B-Policy", environment: "Lokale LIARA-Runtime",
    verifiedAt: "2026-08-16", evidence: ["Judge-Profile fuer alle 6 registrierten Tools (sys, compute.run, compute.generate, orientation, plot_chart, wsl_session)", "Multi-Tool-Turns werden pro Tool-Name mit segmentierten Parametern bewertet, most-restrictive-wins", "Unbekannte Aktionen ohne Profil werden grundsaetzlich blockiert"],
    openGates: ["wsl_session und plot_chart besitzen zwar Judge-Profile, aber ToolExecutor._build_tool_parameters (services/orchestrator/executor.py) hat fuer keines der beiden einen Branch und faellt auf {\"query\": ...} zurueck -- separat als Issue #16 erfasst, nicht hier gefixt"],
  },
  "evidence-engine": {
    level: "controlled-pilot", scope: "EvidenceState-Klassifikation von Tool-Outputs; optionale kanonische EvidenceTarget-Identitaet", environment: "Lokale LIARA-Runtime",
    verifiedAt: "2026-08-16", evidence: ["EvidenceState-Klassifikation (FOUND/NOT_FOUND_IN_SEARCH/UNRESOLVED/CONNECTOR_UNAVAILABLE/ACCESS_DENIED/PRIVATE_CONFIRMED/DOES_NOT_EXIST_CONFIRMED/CONFLICTING_EVIDENCE) laeuft live auf echten Tool-Outputs", "Validator bindet Claims zweistufig an ein aufgeloestes kanonisches Ziel, Mehrdeutigkeit blockiert fail-closed"],
    openGates: ["Kanonische EvidenceTarget-Unterstuetzung (canonical_namespace + canonical_ref) ist implementiert und per synthetischen Tool-Outputs Ende-zu-Ende verifiziert -- aktuell liefert kein realer Connector unter services/tools/ diese Felder. Die Zielidentitaets-Schicht ist bis dahin synthetic-only und noch nicht Teil des Live-Datenflusses; nur die zustandsbasierte EvidenceState-Klassifikation selbst ist live."],
  },
};

export const architectureEvidence: Partial<Record<string, ArchitectureEvidence[]>> = {
  clients: [
    { label: "Admin Console", result: "Dreaming Assurance + Complexity/Coverage table and detail", source: "services/tui/admin_console.py", verifiedAt: "2026-08-08" },
    { label: "API/TUI Regression", result: "40 passed + Textual harness", source: "tests/unit/test_api_app.py + test_admin_console.py", verifiedAt: "2026-08-08" },
    { label: "Servermanager Frontend Build", result: "Build separat / Logstream / expliziter Restart", source: "server_management_gui.py", verifiedAt: "2026-08-08" },
    { label: "Node 24/26 Parallelbetrieb", result: ".next :3001 / .next-node26 :3002 / Canary HTTP 200", source: "server_management_gui.py + next.config.ts", verifiedAt: "2026-08-08" },
    { label: "Same-Origin API-BFF", result: ":3001/api/liara -> 127.0.0.1:8010 / progressives SSE + binaer / Remote-Browser ohne Port-8010-Expose", source: "frontend/web-ui/src/app/api/liara/[...path]/route.ts + src/app/page.tsx", verifiedAt: "2026-08-11" },
  ],
  "openai-bridge": [
    { label: "Implementierter Contract", result: "/v1/chat/completions + /v1/responses", source: "scripts/continue_openai_bridge.py", verifiedAt: "2026-07-15" },
    { label: "Live-Status", result: "HTTP 200 / Adapter auf LIARA API :8010", source: "GET :8011/health", verifiedAt: "2026-08-08" },
  ],
  api: [
    { label: "API Router Modularisierung", result: "8 spezialisierte Subrouter in services/api/routers/ (system, chat, tools, governance, speech, compute, operations, artifacts) 57/57 passed 🟢", source: "services/api/routers/ + tests/unit/test_api_app.py", verifiedAt: "2026-08-12" },
    { label: "Live Health", result: "HTTP 200 / Backend-Aggregat 6/6 healthy in 8.4s", source: "GET /health + /health/backends", verifiedAt: "2026-08-08" },
    { label: "API-Regressionsblock", result: "80 passed", source: "docs/06_build-history/2026-07.md", verifiedAt: "2026-07-14" },
    { label: "Dreaming Operations", result: "Assurance + normalized Quality projection", source: "GET /operations/dreaming", verifiedAt: "2026-08-08" },
    { label: "SYS Audit Tail", result: "Gesamtbestand separat / 200-entry window in single-digit ms", source: "services/tools/builtin/sys_audit.py", verifiedAt: "2026-08-08" },
  ],
  inference: [
    { label: "Speech Codec Stream", result: "8010 -> 8040 / audio_stream/v1 / WebM-Opus default / Ogg-Opus + PCM16", source: "POST /speech/stream + POST /tts/stream", verifiedAt: "2026-08-11" },
    { label: "WebM Live Smoke", result: "First audio 5463 ms / 45315 Bytes / total 14329 ms / WAV-Tee committed", source: "scripts/smoke_tts_stream_proxy.py", verifiedAt: "2026-08-11" },
    { label: "Browser Playback", result: "ReadableStream -> MediaSource -> WebM/Opus / Stop propagiert Abort", source: "frontend/web-ui/src/app/page.tsx", verifiedAt: "2026-08-11" },
    { label: "WAV-Kompatibilität", result: "107520 Frames / 4480 ms / HTTP 200", source: "scripts/smoke_tts_api_proxy.py", verifiedAt: "2026-08-11" },
    { label: "Fokussierte Regression", result: "46 passed / Codec + progressive Ausgabe + Fade + Tee + API", source: "tests/unit/test_audio_streaming.py + test_minicpmo_tts_* + test_tts_service_adapter.py + test_api_app.py", verifiedAt: "2026-08-11" },
  ],
  vision: [
    { label: "Vision Contracts", result: "VisionRequest / VisionResponse / hashgebundene ImageEvidence", source: "services/contracts/vision.py", verifiedAt: "2026-08-11" },
    { label: "VLM Tensor Path", result: "MiniCPM-o VLMPipeline.generate(images=[Tensor], generation_config=...)", source: "services/inference/providers/openvino.py", verifiedAt: "2026-08-11" },
    { label: "Input Boundaries", result: "Web-Upload + OpenAI Data-URI / Remote-URL Default-Deny / keine Base64-History", source: "services/vision/attachments.py + scripts/continue_openai_bridge.py", verifiedAt: "2026-08-11" },
    { label: "Fokussierte Regression", result: "80 passed + Web-UI Production Build", source: "tests/unit/test_vision_pipeline.py + test_continue_openai_bridge.py + test_api_app.py", verifiedAt: "2026-08-11" },
    { label: "E2E Live Smoke", result: "OpenAI Data-URI -> :8011 -> :8010 -> Orchestrator -> :8040 -> Validator / HTTP 200 / Kreis und Farben korrekt", source: "Run f72ce48c-2b28-41ae-9a85-9dfd4854d7b8", verifiedAt: "2026-08-11" },
  ],
  tools: [
    { label: "Julia Compute Runtime", result: "WSL default / Julia 1.12.6 / API smoke HTTP 200", source: "services/simulation/bridge.py + scripts/compute_run_api_smoke_test.py", verifiedAt: "2026-08-10" },
    { label: "Smoke-Isolation", result: "61 passed / llama.cpp PIDs vor und nach Test identisch", source: "tests/conftest.py + tests/unit/test_api_app.py", verifiedAt: "2026-08-10" },
  ],
  simulation: [
    { label: "Julia Bridge", result: "turbine_power primary path / 31.4159 kW / HTTP 200", source: "POST /compute/run via temporary API", verifiedAt: "2026-08-10" },
    { label: "Reasoning-Paritaet", result: "5 passed mit RUN_JULIA_PARITY_TESTS=1", source: "tests/unit/test_reasoning_math_julia_parity.py", verifiedAt: "2026-08-10" },
    { label: "Chat-Mathematik", result: "Live 17 mal 23 -> 391 / Julia primary / kein Fallback", source: "POST /chat/stream + services/simulation/models/chat_math.jl", verifiedAt: "2026-08-10" },
  ],
  governance: [
    { label: "Read-only Web Capability", result: "W/G/B-validierter curl GET läuft; POST, Upload und file:// bleiben Policy-Denial", source: "services/tools/governance.py + services/tools/builtin/sys_command_policy.py", verifiedAt: "2026-08-11" },
    { label: "SYS Governance Flow", result: "Proposal -> Decision -> bound Invoke -> Audit", source: "services/api/app.py", verifiedAt: "2026-08-08" },
    { label: "Governance Regression", result: "digest + policy block + single-use + event sequence", source: "tests/unit/test_api_app.py", verifiedAt: "2026-08-08" },
    { label: "Workspace Handoff", result: "governance_required -> pending proposal -> awaiting_decision", source: "tests/unit/test_workspace_agent.py", verifiedAt: "2026-08-08" },
    { label: "Workspace Auto-Resume", result: "Live: Approval 200 -> 1 Invoke -> Validator passed -> resume_completed; Replay 409", source: "SYS proposal sys-prop-2ba15f6933ca + services/orchestrator/workspace_agent.py", verifiedAt: "2026-08-08" },
    { label: "Apply / Rollback", result: "Live: Apply 200 -> Child-Compensation 1x -> alter SHA-256 restauriert -> Replay 409", source: "SYS proposals sys-prop-e8c94737d54a + sys-prop-4eaf029a3a44", verifiedAt: "2026-08-08" },
    { label: "Apply/Rollback Regression", result: "62 API-/Workspace-Tests bestanden", source: "tests/unit/test_api_app.py + test_workspace_agent.py", verifiedAt: "2026-08-08" },
    { label: "Audit/Governance Regression", result: "38 passed", source: "tests/unit/test_sys_audit.py + test_api_app.py", verifiedAt: "2026-08-08" },
    { label: "Fail-Closed Audit-Reihenfolge", result: "Audit muss der Mutation vorausgehen, nicht folgen", source: "commit 4636620", verifiedAt: "2026-08-15" },
    { label: "Atomarer handoff_key-Dedup", result: "DB-Unique-Index gegen Race-Condition; dabei gefundener Redaction-Bug (Substring-Match maskierte gespeicherte handoff_keys) mitgefixt", source: "commit 39fea06", verifiedAt: "2026-08-15" },
    { label: "Postgres-only Reads + Workspace-Agent-Migration", result: "Proposal-/Event-Reads konsolidiert auf Postgres; workspace_agent auf gemeinsame GovernanceService migriert", source: "commit e73f080 + services/api/services/governance_service.py", verifiedAt: "2026-08-15" },
  ],
  orchestrator: [
    { label: "Orchestrator Modularization", result: "4 Submodule (reasoning_control, librarian_pipeline, tool_discovery, generation_pipeline) + Facade 46/46 passed 🟢", source: "services/orchestrator/ + tests/unit/test_orchestrator*.py", verifiedAt: "2026-08-12" },
    { label: "Nephy Abnahmeprüfung", result: "8/8 Akzeptanzprüfungen grün (Cold Start, E2E Trace, Memory Roundtrip, Facts/Graph, Tools, NPU, Fallback, Operations)", source: "scratch/nephy_acceptance_test_suite.py", verifiedAt: "2026-08-12" },
    { label: "MiniCPM INT4 Helper Gate", result: "VLMPipeline auf NPU aktiv; direkter /infer-Contract ~19 s warm; Retrieval-Produktivgate wegen Wiederholungsdrift nicht freigegeben", source: "services/inference/providers/openvino.py + openvino_npu_helper.py + ADR-005", verifiedAt: "2026-08-11" },
    { label: "Inference-first Web Retrieval", result: "Freie Anfrage -> RetrievalIntent -> 8 Discovery-Kandidaten -> frischer Policy-/Judge-Abruf -> belegte Antwort", source: "ADR-005 + Run 4bede885-9ec3-43f6-8a06-15ceda0e6479", verifiedAt: "2026-08-11" },
    { label: "Discovery-Rank-und-Fetch-Round-Trip", result: "complete_web_discovery/rank_discovery_candidate waren seit der Modularisierung toter Code (nirgends aufgerufen); jetzt in _execute_run_pipeline verdrahtet -- LLM-Kandidatenbewertung zuerst, deterministisches Ranking als Fallback, Gewinner-Fetch durchläuft denselben Pre-Action-Judge/Governance-Pfad wie jeder andere Tool-Aufruf", source: "Issue #22 + tests/unit/test_complete_web_discovery.py", verifiedAt: "2026-08-16" },
    { label: "Retrieval Regression", result: "302 Unit-/Integrationstests bestanden; Discovery allein gilt nicht als Evidenz", source: "tests/unit/test_retrieval_intent.py + tests/unit/test_validator.py + tests/integration/test_orchestrator_judge_flow.py", verifiedAt: "2026-08-11" },
    { label: "System Prompt Phase Contract", result: "Kein fiktives SEARCH-Tool; Antwortsynthese konsumiert abgeschlossene Toolresults und emittiert keine Tooldirektiven", source: "config/system_promt.yaml + services/orchestrator/planner.py", verifiedAt: "2026-08-11" },
    { label: "Scout Vector Integration", result: "SCOUT_USE_REAL_EMBEDDINGS -> 1024D OpenVINO Vectors (:8030) -> Redis Cache -> Cosine Similarity Routing", source: "services/orchestrator/scout_embedding.py + router.py", verifiedAt: "2026-08-11" },
    { label: "Verifizierte Unit-Baseline", result: "40 failed, 1521 passed, 5 skipped -- Fehlschläge vorbestehend (Memory/Reasoning/Relations/Retrieval), keine Evidence-/Judge-/Validator-/Governance-Regression", source: "tests/unit", verifiedAt: "2026-08-16" },
    { label: "Governance Handoff", result: "Exact action binding + idempotent pending proposal", source: "services/orchestrator/workspace_agent.py", verifiedAt: "2026-08-08" },
    { label: "Tool Evidence Integrity", result: "168 API-/Executor-/Validator + 52 Judge-/Coordinator + 28 Orchestrator-Tests bestanden", source: "ADR-004 + tests/unit/test_validator.py + test_orchestration_split.py", verifiedAt: "2026-08-11" },
    { label: "Post-Result Evidence Binding", result: "Erfolgreiche Tooloutputs erreichen Validator und Judge identisch; false no-evidence block geschlossen", source: "services/orchestrator/defs/judge.py + tests/integration/test_orchestrator_judge_flow.py", verifiedAt: "2026-08-11" },
    { label: "Live Failure Canary", result: "Governance block -> evidence=false -> sichere Antwort -> validation passed -> 0 Artefakte", source: "Run 23566477-4f29-4c97-b32f-322a71b7feda", verifiedAt: "2026-08-11" },
  ],
  wsl: [
    { label: "Realer Workspace", result: "5 tests passed", source: "docs/07_tests/test-overview.md", verifiedAt: "2026-07-14" },
    { label: "Mutation", result: "Read-after-write + SHA-256", source: "docs/06_build-history/2026-07.md", verifiedAt: "2026-07-14" },
    { label: "Governance Canary", result: "blocked -> manual approval -> verified write -> replay 409 -> destroy", source: "SYS governance + native WSL session", verifiedAt: "2026-08-08" },
    { label: "WSL Session Contract", result: "API trace context accepted · 6 passed", source: "tests/unit/test_wsl_session_runtime.py", verifiedAt: "2026-08-08" },
  ],
  "ai-validator": [
    { label: "Realer Validator-Job", result: "completed · exit 0 · 0 findings", source: "docs/07_tests/test-overview.md", verifiedAt: "2026-07-14" },
    { label: "Proposal-Bindung", result: "ValidatorJobSubject + SHA-256 digest + strict verdict", source: "services/contracts/validator_jobs.py", verifiedAt: "2026-08-08" },
    { label: "Strukturierte Findings", result: "ValidatorFinding 5-Tupel {file_path, line, rule, severity, message} parst Ruff/Mypy/Pytest", source: "services/contracts/validator_jobs.py + services/validator/parser.py", verifiedAt: "2026-08-11" },
  ],
  memory: [
    { label: "Live Health", result: "HTTP 200 / Backend-Health 6/6 healthy in 0.9s / Neo4j-Readiness entkoppelt", source: "GET :8020/health/backends + docker compose ps", verifiedAt: "2026-08-08" },
    { label: "Neo4j Health Contract", result: "Docker prueft HTTP-Readiness; Memory prueft authentisierte Graph-Funktion", source: "docker-compose.yml + services/memory/store.py", verifiedAt: "2026-08-08" },
    { label: "Architecture Subgraph", result: "11 Knoten / 12 Beziehungen / warm 14-18ms / kalter Aufruf 347ms", source: "GET :8010/operations/graph/subgraph?component=memory&limit=12", verifiedAt: "2026-08-08" },
    { label: "Subgraph-Budget", result: "konfigurierbar 8s / kontrollierte Timeout-Huelle / 10 Tests passed", source: "services/memory/store.py + tests/unit/test_architecture_subgraph.py", verifiedAt: "2026-08-08" },
    { label: "Dreaming/Staging Contract", result: "stage/run + graph + quality signals + assurance + cleanup", source: "services/contracts/memory_dreaming.py", verifiedAt: "2026-08-08" },
    { label: "Assurance-Integration", result: "133 passed", source: "Memory + Validator + Self Observer + Workspace tests", verifiedAt: "2026-08-08" },
    { label: "Memory Retention", result: "109 passed", source: "Memory store + Dreaming contract/service tests", verifiedAt: "2026-08-08" },
  ],
  embedding: [
    { label: "Native Runtime", result: "healthy / openvino-cpp / NPU / uptime ca. 86 min / request_count 0", source: "GET :8030/health", verifiedAt: "2026-08-08" },
    { label: "Vektorcontract", result: "1024 Dimensionen / max length 512", source: "GET :8030/health", verifiedAt: "2026-07-15" },
    { label: "LiNeP", result: "Worker 30 / TCP 8767 / Heartbeat 8768", source: "GET :8030/health", verifiedAt: "2026-07-15" },
  ],
  heartbeat: [
    { label: "Eigene Instanz", result: "Port 8050 · healthy · Trend improving · Stabilitaet 0.88", source: "GET :8050/health + /v1/curve", verifiedAt: "2026-08-08" },
    { label: "Contract", result: "Herstellerneutral · typisiert · versioniert", source: "services/contracts/heartbeat.py", verifiedAt: "2026-07-15" },
    { label: "Backendtests", result: "6 passed", source: "tests/unit/test_heartbeat_service.py", verifiedAt: "2026-07-15" },
  ],
  "self-observer": [
    { label: "Fokussierte Regression", result: "24 passed", source: "tests/unit/test_self_observer.py", verifiedAt: "2026-07-15" },
    { label: "Realer Assurance-Canary", result: "9/9 Korrelation · failed: timeout", source: "artifacts/self_inspection_canary/operator-canary-20260715T185357Z.json", verifiedAt: "2026-07-15" },
    { label: "Zustandscontract", result: "3 Domaenen · Hysterese · Permission-Gate", source: "services/contracts/self_observer.py", verifiedAt: "2026-07-15" },
    { label: "Live-Zustand", result: "degraded · laufende Instanz noch software ReadTimeout · Validator-Evidenz stale/failed", source: "GET :8060/v1/state", verifiedAt: "2026-08-08" },
    { label: "API-Proxy", result: "success · state/history/inspection erreichbar", source: "GET :8010/operations/self-observer", verifiedAt: "2026-08-08" },
    { label: "Probe-Budget", result: "4s schnelle Quellen / 12s Backend-Aggregat · 10 Tests passed", source: "services/self_observer/probes.py + tests/unit/test_self_observer.py", verifiedAt: "2026-08-08" },
  ],
};

export const relationDetails: Record<string, { meaning: string; contract: string }> = {
  "clients:api": { meaning: "Transportiert Nutzerauftrag, Session und Traceability in die API-Grenze; administrative Oberflaechen lesen dort Dreaming-Assurance und Quality-Signale ohne Mutationsrechte.", contract: "ChatRequest / SSE / GET /operations/dreaming" },
  "openai-bridge:api": { meaning: "Adaptiert OpenAI-kompatible IDE- und Client-Aufrufe, ohne selbst Agenten- oder Orchestrierungslogik zu besitzen.", contract: "OpenAI request -> ChatRequest / SSE" },
  "api:profile": { meaning: "Uebergibt die normalisierte Nachricht zur Situationsanalyse.", contract: "InputSituationProfile" },
  "profile:orchestrator": { meaning: "Steuert anhand von Kontext, Domaene, Mood und Handlungsbedarf den naechsten Pfad.", contract: "Situation profile" },
  "orchestrator:vision": { meaning: "Sendet ausschließlich API-normalisierte, transiente Bilddaten an die interne visuelle Wahrnehmung.", contract: "VisionRequest -> POST :8040/vision/analyze" },
  "vision:judge": { meaning: "Nur eine erfolgreiche Beobachtung mit SHA-256-gebundener ImageEvidence darf visuelle Aussagen erden.", contract: "VisionResponse -> tool_results.vision -> vision_evidence_integrity" },
  "orchestrator:planner": { meaning: "Aktiviert Planung nur fuer hinreichend komplexe oder handlungsorientierte Auftraege.", contract: "Typed plan request" },
  "planner:tools": { meaning: "Gibt genau einen typisierten, budgetierten Schritt zur Ausfuehrung frei.", contract: "WorkspaceStep" },
  "tools:judge": { meaning: "Nur das konkret vorbereitete und tatsächlich ausgeführte Payload kann erfolgreiche Evidenz liefern. Failure-Envelopes bleiben diagnostisch sichtbar und erden keine Fakten.", contract: "ToolExecutionRequest -> ToolExecutionResult(evidence) -> ValidationContext" },
  "tools:wsl": { meaning: "Fuehrt eine W/G/B-gepruefte Operation als Nutzer liara im Debian-Workspace aus. Rein lesende, kontextuell validierte HTTP(S)-Abrufe benoetigen im risk_based-Modus keine zweite Freigabe; Blacklist-Treffer bleiben endgueltig gesperrt.", contract: "ToolExecutionRequest -> CommandPolicy(W/G/B) -> SYS Audit" },
  "wsl:verify": { meaning: "Beobachtet den realen Zustand nach einer Mutation und vergleicht ihn mit der Erwartung.", contract: "Mutation evidence" },
  "verify:planner": { meaning: "Nur bestaetigte Schritte erlauben die Fortsetzung des Plans.", contract: "Step observation" },
  "wsl:ai-validator": { meaning: "Uebergibt den fertigen Kandidaten an eine logisch getrennte Validierungsinstanz.", contract: "Validator job" },
  "embedding:memory": { meaning: "Der Memory-Pfad fordert semantische Vektoren an; der native C++-Worker liefert den OpenVINO-Vektorcontract zurueck.", contract: "POST /embedding/generate -> 1024-d vector" },
  "memory:simulation": { meaning: "Staged Memory-Eintraege liefern explizite Konsolidierungssignale. Dreaming kann Summary, direkt gebundene Graph-Evidenz und deterministische Complexity-/Coverage-Evidenz uebernehmen. Retention startet als Dry-run.", contract: "stage -> touch -> run(include_quality_signals) -> proposal; POST /dreaming/cleanup(dry_run=true)" },
  "simulation:governance": { meaning: "Eine simulierte Verbesserung bleibt Proposal. Ist Assurance verlangt, blockiert die Approval bis ein proposal-gebundener strikter Validator-Report den Verdict passed erreicht.", contract: "MemoryDreamingProposal + AssuranceVerdict + Decision" },
  "ai-validator:governance": { meaning: "Der Validator-Report bleibt ueber Proposal-ID und serverseitigen SHA-256-Digest an genau einen unveraenderten Kandidaten gebunden. Attention oder Failed koennen keine assurance-pflichtige Freigabe erzeugen.", contract: "ValidatorJobSubject -> POST /dreaming/proposals/assurance" },
  "governance:tools": { meaning: "Der zentrale Coordinator erlaubt read-only SYS sowie W/G/B-validierte HTTP(S)-Abrufe im risk_based-Modus und fordert fuer Mutationen, freie Codeausfuehrung und unprofilierte sensitive Aktionen eine per SHA-256 gebundene Proposal-ID. Blacklist-Denials sind nicht approvable. Allgemeines Apply bleibt single-use; reversible Datei-Overwrites erhalten eine verifizierte Child-Kompensation.", contract: "CommandPolicy(W/G/B) -> Coordinator Guard -> optional ToolProposal -> Decision -> Apply -> Invoke -> optionaler Snapshot/Child-Rollback -> Governance Event + SYS Audit" },
  "heartbeat:orchestrator": { meaning: "Die Zustandskurve ist implementiert; ihre Ressourcenhuelle soll nach Policy- und Scheduler-Anbindung als Routing-Evidenz dienen.", contract: "StateCurve / ResourceEnvelope (Konsum noch geplant)" },
  "embedding:heartbeat": { meaning: "Der native Embedding-Worker liefert bereits einen realen LiNeP-Slot und Heartbeat als Teilbaustein der spaeteren globalen Ressourcensteuerung.", contract: "LiNeP worker 30 / TCP 8767 / UDP 8768" },
  "api:self-observer": { meaning: "Die Beobachtungsinstanz liest Servicegesundheit und Backend-Zustaende zyklisch ueber bestehende read-only Grenzen.", contract: "StateEvidence(domain=software)" },
  "heartbeat:self-observer": { meaning: "Der bestehende Ressourcen-Heartbeat liefert die Hardwareseite des spaeteren Gesamtzustands.", contract: "HeartbeatSnapshot / StateCurve" },
  "ai-validator:self-observer": { meaning: "Der unabhaengige Validator liefert tiefere Softwareevidenz aus Code-, Contract- und Testpruefungen. Der Self Observer darf sie verdichten, aber weder Findings aufheben noch sich damit selbst freigeben.", contract: "ValidationResult / structured findings" },
  "self-observer:orchestrator": { meaning: "Der SystemStateEnvelope ist implementiert; sein Konsum durch Orchestrator oder spaetere Steuerinstanzen bleibt bewusst offen.", contract: "SystemStateEnvelope (Konsum noch geplant)" },
  "orchestrator:judge": { meaning: "Vor jedem Tool-Dispatch entscheidet ein fail-closed Pre-Action-Gate ueber Erlaubnis, Ablehnung oder Modifikation; unbekannte Aktionen werden ohne Profil grundsaetzlich blockiert. Multi-Tool-Turns werden pro Tool-Name mit segmentierten Parametern bewertet, most-restrictive-wins.", contract: "JudgeContext(stage=pre_action) -> evaluate_pre_action() -> JudgeDecision" },
  "tools:evidence-engine": { meaning: "Jedes Tool-Ergebnis wird zusaetzlich zur direkten Validator-Kante in einen expliziten EvidenceState klassifiziert, damit NO_RESULT nie stillschweigend zu NOT_EXISTS wird.", contract: "ToolExecutionResult -> EvidenceEngine.analyze() -> EvidenceAssertion(EvidenceState)" },
  "evidence-engine:judge": { meaning: "Der Validator bindet einen Claim zweistufig an ein aufgeloestes kanonisches Ziel: zuerst zustandsunabhaengiger Volltext-Identifier-Abgleich (Mehrdeutigkeit blockiert), erst danach die State-Pruefung genau dieses Ziels ohne Fallback auf ein anderes Ziel.", contract: "EvidenceAssertion(EvidenceState, EvidenceTarget?) -> claim binding -> ValidationContext" },
};

export const tracePresets: TracePreset[] = [
  {
    id: "vision-evidence", label: "Bild → Evidence", description: "Vom kontrollierten Bild-Upload bis zur validierten visuellen Beobachtung.", view: "chat",
    nodes: ["clients", "api", "orchestrator", "vision", "judge", "memory"],
    edges: [["clients", "api"], ["api", "orchestrator"], ["orchestrator", "vision"], ["vision", "judge"], ["judge", "memory"]],
  },
  {
    id: "chat-memory", label: "Chat → Memory", description: "Vom Eingang bis zum bewerteten Memory-Write.", view: "chat",
    nodes: ["clients", "api", "profile", "orchestrator", "context", "inference", "judge", "memory"],
    edges: [["clients", "api"], ["api", "profile"], ["profile", "orchestrator"], ["orchestrator", "context"], ["context", "inference"], ["inference", "judge"], ["judge", "memory"]],
  },
  {
    id: "semantic-context", label: "Embedding -> Kontext", description: "Nativer Vektorpfad vom C++/OpenVINO-Worker ueber Memory in den Kontext.", view: "chat",
    nodes: ["embedding", "memory", "context", "inference"],
    edges: [["embedding", "memory"], ["memory", "context"], ["context", "inference"]],
  },
  {
    id: "workspace-validation", label: "Workspace → Validator", description: "Plan, Mutation, Beobachtung und externer Validator.", view: "workspace",
    nodes: ["api", "orchestrator", "planner", "tools", "wsl", "verify", "ai-validator", "memory"],
    edges: [["api", "orchestrator"], ["orchestrator", "planner"], ["planner", "tools"], ["tools", "wsl"], ["wsl", "verify"], ["wsl", "ai-validator"], ["ai-validator", "memory"]],
  },
  {
    id: "controlled-change", label: "Finding → Freigabe", description: "Vom beobachteten Finding bis zur kontrollierten Ausfuehrung.", view: "control",
    nodes: ["memory", "simulation", "governance", "tools", "wsl", "verify"],
    edges: [["memory", "simulation"], ["simulation", "governance"], ["governance", "tools"], ["tools", "wsl"], ["wsl", "verify"]],
  },
  {
    id: "dreaming-staging", label: "Staging -> Dreaming", description: "Staging, Summary, Graph- und Quality-Evidenz werden zum Proposal; optionale Validator-Assurance gate't die Decision.", view: "control",
    nodes: ["memory", "simulation", "ai-validator", "governance"],
    edges: [["memory", "simulation"], ["simulation", "governance"], ["ai-validator", "governance"]],
  },
  {
    id: "tool-evidence", label: "Tool → Evidence → Judge", description: "Vom fail-closed Pre-Action-Gate ueber die reale Tool-Ausfuehrung bis zur EvidenceState-Klassifikation und Claim-Bindung.", view: "chat",
    nodes: ["orchestrator", "tools", "evidence-engine", "judge", "memory"],
    edges: [["orchestrator", "tools"], ["orchestrator", "judge"], ["tools", "evidence-engine"], ["evidence-engine", "judge"], ["judge", "memory"]],
  },
];
