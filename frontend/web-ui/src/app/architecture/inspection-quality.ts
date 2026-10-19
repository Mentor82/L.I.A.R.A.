export type InspectionSnapshot = {
  mode?: string;
  action?: string;
  job_state?: string | null;
  exit_code?: number | null;
  quality_verdict?: string | null;
  findings_total?: number | null;
  highest_severity?: string | null;
  severity_counts?: Record<string, number>;
  disposition_counts?: Record<string, number>;
  findings?: Array<{ severity?: string }>;
};

export function inspectionEvidence(inspection: InspectionSnapshot) {
  const counts = inspection.severity_counts;
  const total = inspection.findings_total;
  const preview = inspection.findings?.length ?? 0;
  const dispositions = inspection.disposition_counts;
  const highest = counts?.error ? "error" : counts?.warning ? "warning" : counts?.info ? "info" : "none";
  const complete = typeof total === "number" && Number.isInteger(total) && total >= preview
    && counts && Object.keys(counts).length === 3
    && ["info", "warning", "error"].every(key => Number.isInteger(counts[key]) && counts[key] >= 0)
    && counts.info + counts.warning + counts.error === total
    && highest === inspection.highest_severity
    && dispositions && Object.keys(dispositions).length === 3
    && ["new", "baseline", "windows_mount"].every(key => Number.isInteger(dispositions[key]) && dispositions[key] >= 0)
    && dispositions.new + dispositions.baseline + dispositions.windows_mount === total
    && (inspection.findings ?? []).every(f => ["info", "warning", "error"].includes(f.severity ?? ""))
    && ["info", "warning", "error"].every(key => (inspection.findings ?? []).filter(f => f.severity === key).length <= counts[key]);
  const expected = counts?.error ? "failed" : counts?.warning ? "attention" : "passed";
  let quality = inspection.quality_verdict ?? "unknown";
  if (inspection.job_state === "failed" || inspection.action === "failed"
      || (typeof inspection.exit_code === "number" && inspection.exit_code !== 0) || counts?.error) {
    quality = "failed";
  } else if (!complete || inspection.job_state !== "completed" || inspection.exit_code !== 0
      || quality !== expected) {
    quality = quality === "failed" ? "failed" : "unknown";
  }
  const accepted = (dispositions?.baseline ?? 0) + (dispositions?.windows_mount ?? 0);
  return [
    {
      label: "Prüfausführung",
      value: `${inspection.mode ?? "unknown"}:${inspection.job_state ?? inspection.action ?? "none"}`,
      detail: "Technischer Status des Prüflaufs",
      state: inspection.job_state === "failed" || inspection.action === "failed" ? "attention" as const : "healthy" as const,
    },
    {
      label: "Assurance-Qualität",
      value: quality,
      detail: complete
        ? `${preview} von ${total} Findings · höchste Severity: ${inspection.highest_severity ?? "unknown"} · neu: ${dispositions?.new ?? "unknown"} · akzeptiert: ${accepted}`
        : "Vollständige Qualitätsdaten fehlen – keine bestandene Assurance bestätigt",
      state: quality === "passed" ? "healthy" as const : "attention" as const,
    },
  ];
}
