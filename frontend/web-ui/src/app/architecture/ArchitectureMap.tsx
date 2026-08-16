"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, Network, RefreshCw } from "lucide-react";
import LiaraLogo from "../components/LiaraLogo";
import {
  architectureEdges,
  architectureEvidence,
  architectureNodes,
  architectureViews,
  readinessClaims,
  relationDetails,
  tracePresets,
  type ArchitectureEdge,
  type ArchitectureNode,
  type ArchitectureStatus,
  type ArchitectureView,
} from "./architecture-data";
import styles from "./architecture.module.css";
import ArchitectureSubgraph from "./ArchitectureSubgraph";
import HeartbeatPanel, { type HeartbeatOperations } from "./HeartbeatPanel";

const statusLabels: Record<ArchitectureStatus, string> = {
  implemented: "Implementiert",
  partial: "Teilweise",
  prepared: "Vorbereitet · nicht verdrahtet",
  planned: "Geplant",
  retired: "Verworfen / inaktiv",
};

const edgeColors = {
  data: "#62dff5",
  decision: "#f2bd58",
  mutation: "#ff7a9c",
  validation: "#77e6ae",
};

const NODE_WIDTH = 150;
const NODE_HEIGHT = 70;

type ArchitectureScope = "actual" | "partial" | "target";

type LiveEvidenceState = {
  loading: boolean;
  refreshedAt?: string;
  error?: string;
  health?: {
    status?: string;
    service?: string;
    memory_mode?: string;
  };
  backends?: {
    backend_health?: Record<string, string>;
    device?: string;
    runtime_backend?: string;
    dimensions?: number;
    effective_max_length?: number;
    configured_model_id?: string;
  };
  audit?: {
    summary?: {
      total?: number;
      allowed?: number;
      blocked?: number;
      failed_allowed?: number;
      high_risk?: number;
      write_ops?: number;
      available_entries?: number;
      inspected_entries?: number;
    };
  };
  governance?: {
    count?: number;
    total?: number;
    summary?: {
      decisions?: Record<string, number>;
      invocation_states?: Record<string, number>;
      policy_blocked?: number;
      consumed?: number;
      enforcement_mode?: string;
    };
    items?: Array<{
      proposal_id?: string;
      decision?: string;
      policy_check?: { allowed?: boolean; risk_level?: string };
      invocation?: { state?: string; attempt_count?: number; success_count?: number };
      audit_reference?: { endpoint?: string };
    }>;
  };
  dreaming?: {
    status?: string;
    scheduler_enabled?: boolean;
    mode?: string;
    last_run_id?: string | null;
    last_run_at?: string | null;
    last_run_state?: string;
    pending_staged_items?: number;
    pending_proposals?: number;
    proposal_count?: number;
    proposals?: Array<{
      proposal_id?: string;
      decision?: string;
      proposed_status?: string;
      created_at?: string;
    }>;
  };
  workspace?: {
    workspace?: {
      store_mode?: string;
      workspace_root?: string;
      exists?: boolean;
      artifact_counts?: Record<string, number>;
    };
    artifacts?: Array<{
      type?: string;
      timestamp?: string;
      summary?: Record<string, string | number | null>;
    }>;
  };
  heartbeat?: HeartbeatOperations;
  selfObserver?: {
    status?: string;
    state?: {
      sequence?: number;
      state?: string;
      phase?: string;
      trend?: string;
      confidence?: number;
      stability?: number;
      quiet_cycles?: number;
      background_analysis_candidate?: boolean;
      signals?: string[];
    } | null;
    history?: Array<{ sequence?: number; state?: string; phase?: string }>;
    inspection?: {
      mode?: string;
      eligible?: boolean;
      action?: string;
      reasons?: string[];
      job_id?: string | null;
      job_state?: string | null;
      completed_at?: string | null;
      findings?: Array<{ severity?: string; message?: string; file_path?: string | null }>;
      artifacts?: string[];
      next_eligible_at?: string | null;
    } | null;
    error?: string | null;
  };
};

type LiveEvidenceItem = {
  label: string;
  value: string;
  detail: string;
  state: "healthy" | "attention";
};

const readinessLabels = {
  development: "Development",
  "controlled-pilot": "Controlled pilot",
  "staging-ready": "Staging-ready",
  "production-capable": "Production-capable",
};

const edgeKey = (edge: Pick<ArchitectureEdge, "from" | "to">) => `${edge.from}:${edge.to}`;

const defaultApiBaseUrl = () => {
  return "/api/liara";
};

const isStatusVisible = (status: ArchitectureStatus, scope: ArchitectureScope) => {
  if (scope === "actual") return status === "implemented";
  if (scope === "partial") return status === "implemented" || status === "partial";
  // target ("Zielbild"): implemented/partial/planned/prepared all show;
  // only retired stays hidden even here -- "prepared" is listed explicitly,
  // not inferred from "not retired", so its scope membership can't drift
  // silently if this function is later restructured.
  return status === "implemented" || status === "partial" || status === "planned" || status === "prepared";
};

function edgePath(from: ArchitectureNode, to: ArchitectureNode) {
  const startX = from.x + NODE_WIDTH;
  const startY = from.y + NODE_HEIGHT / 2;
  const endX = to.x;
  const endY = to.y + NODE_HEIGHT / 2;
  const bend = Math.max(35, Math.abs(endX - startX) * 0.42);
  return `M ${startX} ${startY} C ${startX + bend} ${startY}, ${endX - bend} ${endY}, ${endX} ${endY}`;
}

function edgeLabelPosition(from: ArchitectureNode, to: ArchitectureNode) {
  const x = (from.x + to.x + NODE_WIDTH) / 2 - 50;
  const verticalDistance = to.y - from.y;
  const verticalOffset = Math.abs(verticalDistance) > 80 ? (verticalDistance < 0 ? 34 : -34) : 0;
  const y = (from.y + to.y + NODE_HEIGHT) / 2 - 19 + verticalOffset;
  return { x, y };
}

export default function ArchitectureMap() {
  const [view, setView] = useState<ArchitectureView>("system");
  const [selectedId, setSelectedId] = useState("orchestrator");
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<ArchitectureScope>("target");
  const [selectedEdgeKey, setSelectedEdgeKey] = useState<string | null>(null);
  const [activeTraceId, setActiveTraceId] = useState("");
  const [live, setLive] = useState<LiveEvidenceState>({ loading: true });

  const refreshLiveEvidence = useCallback(async () => {
    setLive((current) => ({ ...current, loading: true, error: undefined }));
    const baseUrl = defaultApiBaseUrl();
    const endpoints = {
      health: "/health",
      backends: "/health/backends",
      audit: "/admin/sys-audit/summary?limit=200",
      governance: "/tools/sys/governance/proposals?decision=all&limit=20",
      dreaming: "/operations/dreaming?decision=pending&limit=20",
      workspace: "/operations/workspace?limit=8",
      heartbeat: "/operations/heartbeat?window_seconds=60",
      selfObserver: "/operations/self-observer?history_limit=60",
    } as const;

    const results = await Promise.allSettled(
      Object.entries(endpoints).map(async ([key, path]) => {
        const response = await fetch(`${baseUrl}${path}`, { cache: "no-store" });
        if (!response.ok) throw new Error(`${key}: HTTP ${response.status}`);
        return [key, await response.json()] as const;
      }),
    );
    const fulfilled = results
      .filter((result): result is PromiseFulfilledResult<readonly [string, unknown]> => result.status === "fulfilled")
      .map((result) => result.value);
    const failures = results
      .filter((result): result is PromiseRejectedResult => result.status === "rejected")
      .map((result) => result.reason instanceof Error ? result.reason.message : String(result.reason));

    setLive((current) => ({
      ...current,
      ...Object.fromEntries(fulfilled),
      loading: false,
      refreshedAt: new Date().toISOString(),
      error: failures.length > 0 ? failures.join(" · ") : undefined,
    }));
  }, []);

  useEffect(() => {
    const initial = window.setTimeout(() => void refreshLiveEvidence(), 0);
    const timer = window.setInterval(() => void refreshLiveEvidence(), 30_000);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
    };
  }, [refreshLiveEvidence]);

  const visibleNodes = useMemo(
    () => architectureNodes.filter((node) => node.views.includes(view) && isStatusVisible(node.status, scope)),
    [view, scope],
  );
  const visibleIds = useMemo(() => new Set(visibleNodes.map((node) => node.id)), [visibleNodes]);
  const visibleEdges = useMemo(
    () => architectureEdges.filter((edge) => edge.views.includes(view) && visibleIds.has(edge.from) && visibleIds.has(edge.to)),
    [view, visibleIds],
  );
  const selected = visibleNodes.find((node) => node.id === selectedId) ?? visibleNodes[0];
  const connected = visibleEdges.filter((edge) => edge.from === selected?.id || edge.to === selected?.id);
  const selectedRelation = selectedEdgeKey ? visibleEdges.find((edge) => edgeKey(edge) === selectedEdgeKey) : undefined;
  const activeTrace = tracePresets.find((trace) => trace.id === activeTraceId);
  const traceNodeIds = new Set(activeTrace?.nodes ?? []);
  const traceEdgeIds = new Set((activeTrace?.edges ?? []).map(([from, to]) => `${from}:${to}`));
  const matches = query.trim()
    ? architectureNodes.filter((node) => `${node.title} ${node.subtitle} ${node.layer}`.toLowerCase().includes(query.toLowerCase()))
    : [];
  const readiness = selected ? readinessClaims[selected.id] : undefined;
  const evidence = selected ? architectureEvidence[selected.id] ?? [] : [];
  const relationFrom = selectedRelation ? architectureNodes.find((node) => node.id === selectedRelation.from) : undefined;
  const relationTo = selectedRelation ? architectureNodes.find((node) => node.id === selectedRelation.to) : undefined;
  const backendEntries = Object.entries(live.backends?.backend_health ?? {});
  const healthyBackends = backendEntries.filter(([, status]) => status === "healthy").length;
  const pendingGovernance = live.governance?.summary?.decisions?.pending ?? 0;
  const pendingDreamingProposals = live.dreaming?.pending_proposals ?? 0;
  const pendingStagedItems = live.dreaming?.pending_staged_items ?? 0;
  const artifactCounts = live.workspace?.workspace?.artifact_counts ?? {};
  const validationArtifacts = artifactCounts.validation ?? 0;
  const latestValidation = live.workspace?.artifacts?.find((artifact) => artifact.type?.includes("validation"));

  const selectedLiveEvidence = useMemo<LiveEvidenceItem[]>(() => {
    if (!selected) return [];
    if (selected.id === "api") {
      return live.health ? [{
        label: "API",
        value: live.health.status === "ok" ? "Erreichbar" : String(live.health.status ?? "Unbekannt"),
        detail: `${live.health.service ?? "liara-api"} · Memory ${live.health.memory_mode ?? "unbekannt"}`,
        state: live.health.status === "ok" ? "healthy" : "attention",
      }] : [];
    }
    if (selected.id === "memory" || selected.id === "context") {
      return backendEntries.map(([name, status]) => ({
        label: name,
        value: status,
        detail: `${live.backends?.runtime_backend ?? "Backend"}${live.backends?.device ? ` · ${live.backends.device}` : ""}`,
        state: status === "healthy" ? "healthy" : "attention",
      }));
    }
    if (selected.id === "embedding" && live.backends) {
      const status = live.backends.backend_health?.embedding ?? "unbekannt";
      const items: LiveEvidenceItem[] = [{
        label: "Native Runtime",
        value: status,
        detail: `${live.backends.runtime_backend ?? "Runtime unbekannt"}${live.backends.device ? ` / ${live.backends.device}` : ""}`,
        state: status === "healthy" ? "healthy" : "attention",
      }];
      if (live.backends.dimensions) {
        items.push({
          label: "Vektorcontract",
          value: `${live.backends.dimensions} Dimensionen`,
          detail: `Maximale Laenge ${live.backends.effective_max_length ?? "unbekannt"}`,
          state: "healthy",
        });
      }
      return items;
    }
    if (selected.id === "self-observer" && live.selfObserver?.state) {
      const observer = live.selfObserver.state;
      const items: LiveEvidenceItem[] = [
        {
          label: "Gesamtzustand",
          value: String(observer.state ?? "unknown"),
          detail: `${observer.phase ?? "observing"} · Trend ${observer.trend ?? "unknown"} · Sequenz ${observer.sequence ?? 0}`,
          state: observer.state === "healthy" ? "healthy" : "attention",
        },
        {
          label: "Ruhebeobachtung",
          value: observer.background_analysis_candidate ? "stabil" : "beobachtet",
          detail: `${observer.quiet_cycles ?? 0} stabile Zyklen · keine Arbeitsfreigabe`,
          state: "healthy",
        },
      ];
      if (live.selfObserver.inspection) {
        const inspection = live.selfObserver.inspection;
        const findingCount = inspection.findings?.length ?? 0;
        const gateValue = inspection.job_id
          ? `${inspection.mode ?? "unknown"}:${inspection.job_state ?? inspection.action ?? "none"}`
          : `${inspection.mode ?? "unknown"}:${inspection.action ?? "none"}`;
        items.push({
          label: "Assurance-Gate",
          value: gateValue,
          detail: findingCount > 0
            ? `${findingCount} strukturierte Findings · ${inspection.findings?.[0]?.severity ?? "unknown"}`
            : inspection.reasons?.length
              ? inspection.reasons.join(", ")
              : "Pruefzyklus waere freigegeben",
          state: inspection.action === "failed" || findingCount > 0 ? "attention" : "healthy",
        });
      }
      return items;
    }
    if (selected.id === "tools" || selected.id === "verify") {
      const summary = live.audit?.summary;
      return summary ? [
        { label: "SYS Audit", value: `${summary.available_entries ?? summary.total ?? 0} gesamt`, detail: `Letzte ${summary.inspected_entries ?? summary.total ?? 0} ausgewertet / ${summary.blocked ?? 0} blockiert`, state: (summary.blocked ?? 0) > 0 ? "attention" : "healthy" },
        { label: "Mutationen im Fenster", value: `${summary.write_ops ?? 0} Writes`, detail: `${summary.failed_allowed ?? 0} erlaubte Aufrufe fehlgeschlagen`, state: (summary.failed_allowed ?? 0) > 0 ? "attention" : "healthy" },
      ] : [];
    }
    if (selected.id === "governance") {
      if (!live.governance) return [];
      const governanceSummary = live.governance.summary;
      const consumed = governanceSummary?.consumed ?? 0;
      const policyBlocked = governanceSummary?.policy_blocked ?? 0;
      const failed = governanceSummary?.invocation_states?.failed ?? 0;
      const enforcementMode = governanceSummary?.enforcement_mode ?? "off";
      return [
        { label: "Enforcement", value: enforcementMode, detail: `${pendingGovernance} offen · ${live.governance.total ?? 0} gesamt`, state: enforcementMode === "off" ? "attention" : "healthy" },
        { label: "Verbrauchte Freigaben", value: String(consumed), detail: "Erfolgreich ausgefuehrte, limitierte Autorisierungen", state: "healthy" },
        { label: "Policy / Invocation", value: `${policyBlocked} blockiert`, detail: `${failed} fehlgeschlagene Ausfuehrungen · Audit je Proposal`, state: policyBlocked > 0 || failed > 0 ? "attention" : "healthy" },
      ];
    }
    if (selected.id === "simulation") {
      return live.dreaming ? [
        {
          label: "Dreaming-Modus",
          value: String(live.dreaming.mode ?? "manual_only"),
          detail: live.dreaming.scheduler_enabled ? "Scheduler aktiv" : "manuell/ops-getriggert",
          state: live.dreaming.scheduler_enabled ? "attention" : "healthy",
        },
        {
          label: "Staging",
          value: `${pendingStagedItems} staged`,
          detail: `${pendingDreamingProposals} offene Dreaming-Proposals`,
          state: pendingStagedItems > 0 || pendingDreamingProposals > 0 ? "attention" : "healthy",
        },
      ] : [];
    }
    if (selected.id === "wsl" || selected.id === "ai-validator") {
      const root = live.workspace?.workspace?.workspace_root ?? "nicht gemeldet";
      const localDrift = live.workspace?.workspace?.store_mode !== "wsl" && /^(?:[A-Za-z]:)?[\\/]home[\\/]liara/i.test(root);
      return live.workspace ? [
        { label: "Artefakt-Root", value: live.workspace.workspace?.exists ? "Vorhanden" : "Fehlt", detail: root, state: live.workspace.workspace?.exists && !localDrift ? "healthy" : "attention" },
        { label: "Validator-Reports", value: String(validationArtifacts), detail: latestValidation?.timestamp ?? "Kein aktueller Report", state: validationArtifacts > 0 ? "healthy" : "attention" },
      ] : [];
    }
    return [];
  }, [backendEntries, latestValidation?.timestamp, live, pendingDreamingProposals, pendingGovernance, pendingStagedItems, selected, validationArtifacts]);

  const liveNodeState = (nodeId: string): "healthy" | "attention" | undefined => {
    if (nodeId === "api" && live.health) return live.health.status === "ok" ? "healthy" : "attention";
    if ((nodeId === "memory" || nodeId === "context") && backendEntries.length > 0) return healthyBackends === backendEntries.length ? "healthy" : "attention";
    if (nodeId === "embedding" && live.backends) return live.backends.backend_health?.embedding === "healthy" ? "healthy" : "attention";
    if (nodeId === "heartbeat" && live.heartbeat?.curve) return live.heartbeat.curve.state === "healthy" ? "healthy" : "attention";
    if ((nodeId === "tools" || nodeId === "verify") && live.audit) return (live.audit.summary?.failed_allowed ?? 0) > 0 ? "attention" : "healthy";
    if (nodeId === "governance" && live.governance) return pendingGovernance > 0 ? "attention" : "healthy";
    if (nodeId === "simulation" && live.dreaming) return pendingDreamingProposals > 0 || pendingStagedItems > 0 ? "attention" : "healthy";
    if ((nodeId === "wsl" || nodeId === "ai-validator") && live.workspace) return live.workspace.workspace?.exists ? "healthy" : "attention";
    return undefined;
  };

  const chooseView = (nextView: ArchitectureView) => {
    setView(nextView);
    setActiveTraceId("");
    setSelectedEdgeKey(null);
    const first = architectureNodes.find((node) => node.views.includes(nextView));
    if (first) setSelectedId(first.id);
  };

  const chooseTrace = (traceId: string) => {
    setActiveTraceId(traceId);
    setSelectedEdgeKey(null);
    const trace = tracePresets.find((item) => item.id === traceId);
    if (trace) {
      setView(trace.view);
      setScope("target");
      setSelectedId(trace.nodes[0]);
    }
  };

  const chooseNode = (nodeId: string) => {
    setSelectedId(nodeId);
    setSelectedEdgeKey(null);
  };

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <div className={styles.headerIdentity}>
          <LiaraLogo size={38} />
          <div>
            <div className={styles.eyebrow}><Network size={13} aria-hidden="true" /> LIARA · Living Architecture</div>
            <h1>Architecture Map</h1>
            <p>Beziehungen, Flüsse und Reifegrad vom Zielbild bis zum implementierten Codepfad.</p>
          </div>
        </div>
        <Link className={styles.backLink} href="/"><ArrowLeft size={15} aria-hidden="true" /> Zur LIARA Web UI</Link>
      </header>

      <section className={styles.toolbar} aria-label="Architekturansicht auswählen">
        <div className={styles.tabs} role="tablist">
          {architectureViews.map((item) => (
            <button
              key={item.id}
              className={view === item.id ? styles.activeTab : styles.tab}
              onClick={() => chooseView(item.id)}
              role="tab"
              aria-selected={view === item.id}
              title={item.description}
            >
              {item.label}
            </button>
          ))}
        </div>
        <div className={styles.searchWrap}>
          <label htmlFor="architecture-search">Komponente finden</label>
          <input
            id="architecture-search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="z. B. Validator"
          />
          {matches.length > 0 && (
            <div className={styles.searchResults}>
              {matches.slice(0, 6).map((node) => (
                <button key={node.id} onClick={() => { chooseNode(node.id); setView(node.views[0]); setScope("target"); setQuery(""); }}>
                  <strong>{node.title}</strong><span>{node.layer}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </section>

      <section className={styles.viewControls} aria-label="Darstellung und Trace steuern">
        <label htmlFor="architecture-scope">
          <span>Architekturstand</span>
          <select id="architecture-scope" value={scope} onChange={(event) => { setScope(event.target.value as ArchitectureScope); setSelectedEdgeKey(null); }}>
            <option value="actual">Nur implementiert</option>
            <option value="partial">Ist + teilweise</option>
            <option value="target">Zielbild inklusive geplant</option>
          </select>
        </label>
        <label htmlFor="architecture-trace">
          <span>Trace-Modus</span>
          <select id="architecture-trace" value={activeTraceId} onChange={(event) => chooseTrace(event.target.value)}>
            <option value="">Kein Trace hervorgehoben</option>
            {tracePresets.map((trace) => <option key={trace.id} value={trace.id}>{trace.label}</option>)}
          </select>
        </label>
        {activeTrace && <p><strong>{activeTrace.label}:</strong> {activeTrace.description}</p>}
      </section>

      <section className={styles.liveBar} aria-label="Operative Live-Evidenz">
        <div>
          <span>System</span>
          <strong>{live.health?.status === "ok" ? "API online" : live.loading ? "Lade…" : "API unbekannt"}</strong>
          <small>{backendEntries.length > 0 ? `${healthyBackends}/${backendEntries.length} Backends healthy` : "Backendstatus ausstehend"}</small>
        </div>
        <div>
          <span>SYS Audit</span>
          <strong>{live.audit?.summary?.available_entries ?? "–"} Ereignisse gesamt</strong>
          <small>{live.audit?.summary ? `Letzte ${live.audit.summary.inspected_entries ?? 0}: ${live.audit.summary.blocked ?? 0} blockiert / ${live.audit.summary.high_risk ?? 0} high risk` : "Auditstatus ausstehend"}</small>
        </div>
        <div>
          <span>Control & Assurance</span>
          <strong>{pendingGovernance} offene Proposals</strong>
          <small>{validationArtifacts} Validator-Reports</small>
        </div>
        <div>
          <span>Dreaming</span>
          <strong>{pendingDreamingProposals} pending</strong>
          <small>{pendingStagedItems} staged · {live.dreaming?.mode ?? "manual_only"}</small>
        </div>
        <button type="button" onClick={() => void refreshLiveEvidence()} disabled={live.loading}>
          <RefreshCw size={14} aria-hidden="true" /> {live.loading ? "Aktualisiere…" : "Live aktualisieren"}
        </button>
        <p>{live.error ? `Teilweise nicht erreichbar: ${live.error}` : live.refreshedAt ? `Stand ${new Date(live.refreshedAt).toLocaleTimeString("de-DE")}` : "Live-Daten werden geladen"}</p>
      </section>

      <div className={styles.legend} aria-label="Legende">
        <span><i className={styles.implemented} />Implementiert</span>
        <span><i className={styles.partial} />Teilweise</span>
        <span><i className={styles.planned} />Geplant</span>
        <span><i className={styles.prepared} />Vorbereitet · nicht verdrahtet</span>
        <span className={styles.legendLine}><i />Daten</span>
        <span className={styles.legendDecision}><i />Entscheidung</span>
        <span className={styles.legendMutation}><i />Mutation</span>
        <span className={styles.legendValidation}><i />Prüfung</span>
      </div>

      <section className={styles.workspace}>
        <div className={styles.diagram} aria-label={`${architectureViews.find((item) => item.id === view)?.label} Diagramm`}>
          <svg viewBox="0 0 1160 590" role="img" aria-labelledby="architecture-title architecture-desc">
            <title id="architecture-title">{`LIARA Architektur – ${view}`}</title>
            <desc id="architecture-desc">Interaktives Diagramm der LIARA-Komponenten und ihrer gerichteten Beziehungen.</desc>
            <defs>
              {Object.entries(edgeColors).map(([kind, color]) => (
                <marker key={kind} id={`arrow-${kind}`} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
                  <path d="M 0 0 L 8 4 L 0 8 z" fill={color} />
                </marker>
              ))}
            </defs>
            <g className={styles.edges}>
              {visibleEdges.map((edge) => {
                const from = architectureNodes.find((node) => node.id === edge.from)!;
                const to = architectureNodes.find((node) => node.id === edge.to)!;
                const path = edgePath(from, to);
                const key = edgeKey(edge);
                const traceDimmed = activeTrace && !traceEdgeIds.has(key);
                return (
                  <g
                    key={`${edge.from}-${edge.to}-${edge.label}`}
                    className={`${styles.edge} ${traceDimmed ? styles.dimmed : ""} ${traceEdgeIds.has(key) ? styles.traceActive : ""} ${selectedEdgeKey === key ? styles.edgeSelected : ""}`}
                    onClick={() => setSelectedEdgeKey(key)}
                    onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") setSelectedEdgeKey(key); }}
                    role="button"
                    tabIndex={0}
                    aria-label={`Beziehung ${from.title} zu ${to.title}: ${edge.label}`}
                  >
                    <path className={styles.edgeHit} d={path} />
                    <path d={path} stroke={edgeColors[edge.kind]} markerEnd={`url(#arrow-${edge.kind})`} />
                  </g>
                );
              })}
            </g>
            <g className={styles.edgeLabels}>
              {visibleEdges.map((edge) => {
                const from = architectureNodes.find((node) => node.id === edge.from)!;
                const to = architectureNodes.find((node) => node.id === edge.to)!;
                const key = edgeKey(edge);
                const labelPosition = edgeLabelPosition(from, to);
                return (
                  <foreignObject
                    key={`label-${key}`}
                    className={`${activeTrace && !traceEdgeIds.has(key) ? styles.dimmed : ""} ${selectedEdgeKey === key ? styles.edgeLabelSelected : ""}`}
                    x={labelPosition.x}
                    y={labelPosition.y}
                    width="100"
                    height="24"
                  >
                    <button
                      className={styles.edgeLabelButton}
                      onClick={() => setSelectedEdgeKey(key)}
                      aria-label={`${edge.label}: ${from.title} zu ${to.title}`}
                    >
                      {edge.label}
                    </button>
                  </foreignObject>
                );
              })}
            </g>
            <g>
              {visibleNodes.map((node) => (
                <g
                  key={node.id}
                  className={`${styles.node} ${styles[node.status]} ${selected?.id === node.id && !selectedRelation ? styles.selected : ""} ${activeTrace && !traceNodeIds.has(node.id) ? styles.dimmed : ""} ${traceNodeIds.has(node.id) ? styles.traceNode : ""}`}
                  transform={`translate(${node.x} ${node.y})`}
                  onClick={() => chooseNode(node.id)}
                  onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") chooseNode(node.id); }}
                  role="button"
                  tabIndex={0}
                  aria-label={`${node.title}, ${statusLabels[node.status]}`}
                >
                  <rect width={NODE_WIDTH} height={NODE_HEIGHT} rx="10" />
                  <circle cx="14" cy="14" r="4" />
                  {liveNodeState(node.id) && <circle className={styles[liveNodeState(node.id)!]} cx="136" cy="14" r="4" />}
                  <text className={styles.nodeTitle} x="14" y="36">{node.title}</text>
                  <text className={styles.nodeSubtitle} x="14" y="54">{node.subtitle.length > 25 ? `${node.subtitle.slice(0, 24)}…` : node.subtitle}</text>
                </g>
              ))}
            </g>
          </svg>

          <div className={styles.mobileNodes}>
            {visibleNodes.map((node) => (
              <button key={node.id} className={selected?.id === node.id ? styles.mobileSelected : ""} onClick={() => chooseNode(node.id)}>
                <i className={styles[node.status]} />
                <span><strong>{node.title}</strong><small>{node.subtitle}</small></span>
              </button>
            ))}
          </div>
        </div>

        {(selected || selectedRelation) && (
          <aside className={styles.details} aria-live="polite">
            {selectedRelation && relationFrom && relationTo ? (
              <>
                <div className={styles.detailMeta}>
                  <span>Beziehung · {selectedRelation.kind}</span>
                  <span className={styles.relationStatus}>Kante ausgewählt</span>
                </div>
                <h2>{selectedRelation.label}</h2>
                <p>{relationDetails[edgeKey(selectedRelation)]?.meaning ?? "Gerichtete Beziehung zwischen zwei LIARA-Komponenten."}</p>

                <h3>Quelle → Ziel</h3>
                <div className={styles.relationEndpoints}>
                  <button onClick={() => chooseNode(relationFrom.id)}><small>Quelle</small><strong>{relationFrom.title}</strong></button>
                  <span aria-hidden="true">→</span>
                  <button onClick={() => chooseNode(relationTo.id)}><small>Ziel</small><strong>{relationTo.title}</strong></button>
                </div>

                <h3>Contract / Signal</h3>
                <div className={styles.paths}><code>{relationDetails[edgeKey(selectedRelation)]?.contract ?? selectedRelation.label}</code></div>

                <h3>Sichtbarkeit</h3>
                <p className={styles.compactText}>{selectedRelation.views.map((item) => architectureViews.find((candidate) => candidate.id === item)?.label).join(" · ")}</p>
              </>
            ) : selected ? (
              <>
                <div className={styles.detailMeta}>
                  <span>{selected.layer}</span>
                  <span className={styles[selected.status]}>{statusLabels[selected.status]}</span>
                </div>
                <h2>{selected.title}</h2>
                <p>{selected.description}</p>

                {selected.id === "heartbeat" && (
                  <HeartbeatPanel initial={live.heartbeat} />
                )}

                {readiness && (
                  <section className={styles.readiness} aria-label="Readiness-Nachweis">
                    <div><span>Readiness</span><strong>{readinessLabels[readiness.level]}</strong></div>
                    <p>{readiness.scope}</p>
                    <dl>
                      <div><dt>Umgebung</dt><dd>{readiness.environment}</dd></div>
                      <div><dt>Geprüft</dt><dd>{readiness.verifiedAt}</dd></div>
                    </dl>
                    {readiness.openGates.length > 0 && <p><b>Offene Gates:</b> {readiness.openGates.join(" · ")}</p>}
                  </section>
                )}

                <h3>Verantwortung</h3>
                <ul>{selected.responsibilities.map((item) => <li key={item}>{item}</li>)}</ul>

                <h3>Implementierung</h3>
                <div className={styles.paths}>{selected.paths.map((path) => <code key={path}>{path}</code>)}</div>

                {evidence.length > 0 && (
                  <>
                    <h3>Evidenz</h3>
                    <div className={styles.evidenceList}>
                      {evidence.map((item) => (
                        <div key={`${item.label}-${item.source}`}>
                          <span>{item.label}<small>{item.verifiedAt}</small></span>
                          <strong>{item.result}</strong>
                          <code>{item.source}</code>
                        </div>
                      ))}
                    </div>
                  </>
                )}

                {selectedLiveEvidence.length > 0 && (
                  <>
                    <h3>Live-Evidenz</h3>
                    <div className={styles.liveEvidenceList}>
                      {selectedLiveEvidence.map((item) => (
                        <div key={`${item.label}-${item.detail}`}>
                          <i className={styles[item.state]} aria-hidden="true" />
                          <span><strong>{item.label}</strong><small>{item.detail}</small></span>
                          <b>{item.value}</b>
                        </div>
                      ))}
                    </div>
                  </>
                )}

                {(selected.id === "orchestrator" || selected.id === "memory") && (
                  <ArchitectureSubgraph component={selected.id} />
                )}

                <h3>Direkte Beziehungen</h3>
                <div className={styles.relations}>
                  {connected.map((edge) => {
                    const outbound = edge.from === selected.id;
                    const peer = architectureNodes.find((node) => node.id === (outbound ? edge.to : edge.from));
                    return peer ? (
                      <button key={`${edge.from}-${edge.to}-${edge.label}`} onClick={() => setSelectedEdgeKey(edgeKey(edge))}>
                        <span>{outbound ? "→" : "←"} {edge.label}</span><strong>{peer.title}</strong>
                      </button>
                    ) : null;
                  })}
                </div>
              </>
            ) : null}
          </aside>
        )}
      </section>

      <footer className={styles.footer}>
        <span>Quelle: implementierter Workspace, LIARA-Dokumentation und read-only Live-Evidenz</span>
        <span>{architectureNodes.length} Komponenten · {architectureEdges.length} Beziehungen · datengetrieben erweiterbar</span>
      </footer>
    </main>
  );
}
