"""Full-result quality projection. Display previews must never decide quality."""

QUALITY_FIELDS = ("quality_verdict", "findings_total", "severity_counts", "disposition_counts", "highest_severity")


def verdict(state, exit_code, counts):
    if state == "failed" or counts.get("error", 0) or (type(exit_code) is int and exit_code != 0):
        return "failed"
    if state != "completed" or type(exit_code) is not int:
        return "unknown"
    return "attention" if counts.get("warning", 0) else "passed"


def project_result(result):
    counts = {"info": 0, "warning": 0, "error": 0}
    dispositions = {"new": 0, "baseline": 0, "windows_mount": 0}
    for finding in result.findings:
        counts[finding.severity] += 1
        disposition = finding.metadata.get("disposition", "new")
        dispositions[disposition if disposition in dispositions else "new"] += 1
    quality = verdict(result.state, result.summary.get("exit_code"), counts)
    if result.status.status == "failed":
        quality = "failed"
    declared = result.summary.get("quality_verdict")
    if declared is not None and declared != quality:
        quality = "failed" if quality == "failed" or declared == "failed" else "unknown"
    return dict(quality_verdict=quality, findings_total=len(result.findings), severity_counts=counts,
                disposition_counts=dispositions,
                highest_severity=next((s for s in ("error", "warning", "info") if counts[s]), "none"))


def decision_quality(decision):
    """Persisted legacy/malformed summaries cannot be reconstructed from a preview."""
    counts = decision.severity_counts
    dispositions = decision.disposition_counts
    preview = {"info": 0, "warning": 0, "error": 0}
    for finding in decision.findings:
        preview[finding.severity] += 1
    if (decision.findings_total is None or set(counts) != {"info", "warning", "error"}
            or any(type(n) is not int or n < 0 for n in counts.values())
            or sum(counts.values()) != decision.findings_total
            or set(dispositions) != {"new", "baseline", "windows_mount"}
            or any(type(n) is not int or n < 0 for n in dispositions.values())
            or sum(dispositions.values()) != decision.findings_total
            or any(preview[k] > counts[k] for k in preview)
            or len(decision.findings) > decision.findings_total
            or decision.highest_severity != next((s for s in ("error", "warning", "info") if counts[s]), "none")):
        return "failed" if decision.job_state == "failed" or (type(decision.exit_code) is int and decision.exit_code != 0) else "unknown"
    expected = verdict(decision.job_state, decision.exit_code, counts)
    if decision.quality_verdict != expected:
        return "failed" if expected == "failed" or decision.quality_verdict == "failed" else "unknown"
    return expected


def copy_quality(decision):
    return {name: getattr(decision, name) for name in QUALITY_FIELDS} if decision else {}
