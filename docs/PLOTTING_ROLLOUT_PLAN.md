# Plotting Feature Rollout Plan

## Overview
The plotting feature is production-ready and has been thoroughly tested. This document outlines the phased rollout strategy for enabling chart artifact generation in the LIARA chat system.

## Feature Status
- **Backend**: ✅ Fully implemented and tested (PlotChartTool, artifact extraction, secure file serving)
- **API**: ✅ Complete with `/files/artifact` endpoint and SSE artifact events
- **Frontend**: ✅ GTK and Textual UIs ready for rendering
- **Tests**: ✅ All 7 artifact-related tests passing (4 API tests + 3 tool tests)
- **Logging**: ✅ Instrumented for monitoring artifact generation, extraction, and URL enrichment
- **Feature Flag**: ✅ `PLOTTING_TOOLS_ENABLED` available for gradual enablement

## Rollout Phases

### Phase 1: Internal Testing (Current)
**Duration**: Immediate  
**Scope**: Internal developers and testing environments  
**Actions**:
- Keep `PLOTTING_TOOLS_ENABLED=true` (default)
- Monitor logs for artifact generation, file sizes, and errors
- Run regression tests to ensure Decision Explanation layer and validation metadata remain intact
- Validate GTK and Textual UX with sample plotting requests

**Success Criteria**:
- No regressions in existing validation/decision-explanation features
- Artifact generation logs show no errors
- File sizes are reasonable (typical: 50-150 KB per chart)
- Frontend rendering works smoothly

**Commands**:
```bash
# Monitor plotting activity
tail -f logs/api.log | grep -E "artifact|chart"

# Run regression tests
pytest tests/unit/test_api_app.py -k artifact -q
pytest tests/unit/test_plot_chart_tool.py -q

# Check feature flag status
echo $PLOTTING_TOOLS_ENABLED  # Should be "true"
```

### Phase 2: Staged Release (Post-Internal Review)
**Duration**: 1-2 weeks  
**Scope**: Beta testers and early adopters  
**Actions**:
- Deploy to staging environment with `PLOTTING_TOOLS_ENABLED=true`
- Gather user feedback on chart quality and rendering
- Monitor error rates and artifact URL access patterns
- Test concurrent artifact generation and caching behavior
- Validate artifact cleanup and sandbox isolation

**Success Criteria**:
- No spike in API error rates
- Artifact URLs resolve correctly across sessions
- File system doesn't accumulate orphaned artifacts
- Performance impact is negligible (<50ms additional per response)

### Phase 3: Full Production Release
**Duration**: Week 3+  
**Scope**: All users  
**Actions**:
- Deploy to production with `PLOTTING_TOOLS_ENABLED=true`
- Enable artifact collection in monitoring dashboards
- Set up alerts for:
  - High artifact generation error rates (>5%)
  - Excessive artifact file sizes (>500 KB)
  - Cross-session access attempts (security)
  - Artifact endpoint latency (>500ms)

**Success Criteria**:
- Deployment without incidents
- Artifact generation success rate >98%
- User satisfaction feedback on plotting feature

## Feature Flag Configuration

### Environment Variable
```bash
PLOTTING_TOOLS_ENABLED=true   # Enable chart generation (default: true)
PLOTTING_TOOLS_ENABLED=false  # Disable (fallback mode)
```

### Behavior
- **Enabled** (`true`): Plot chart tool is registered and available; artifacts extracted and enriched automatically
- **Disabled** (`false`): Plot chart tool not registered; responses never contain artifacts; no performance impact

## Monitoring & Observability

### Logs to Watch
```
liara.api.artifacts      # Artifact extraction and URL enrichment
liara.tools.builtin      # Plot chart tool execution (DEBUG level shows chart details)
liara.api.chat_stream    # SSE events including artifact emissions
liara.api.chat_run       # Chat run completion with artifact metadata
```

### Example Log Entries
```
INFO liara.api.artifacts: Extracted 1 artifact(s) from tool_results (tools: plot_chart)
DEBUG liara.api.artifacts: Generated artifact URL: kind=image, title=Sales Trend, url_path=.liara_artifacts/abc123/chart_xyz.png
INFO services.tools.builtin: Chart artifact generated: chart_xyz.png (87.3 KB)
```

### Metrics to Track
- `artifact_generation_success_rate` (%)
- `artifact_generation_duration_ms` (median, p95, p99)
- `artifact_file_size_kb` (distribution)
- `artifact_endpoint_access_count` (per session)
- `artifact_extraction_latency_ms` (per request)

## Rollback Plan

If critical issues arise:

1. **Immediate**: Set `PLOTTING_TOOLS_ENABLED=false` in all environments
2. **Impact**: Existing responses remain unchanged; new responses won't have artifacts
3. **Investigation**: Review logs for error patterns; check artifact file system health
4. **Fix & Retry**: Apply patch; test thoroughly; re-enable with reduced scope

**Rollback Commands**:
```bash
# Quick disable
export PLOTTING_TOOLS_ENABLED=false

# Verify disable
curl http://api:8010/health  # Should show plotting disabled in response metadata

# Manual cleanup if needed
find .liara_artifacts -type f -mtime +7 -delete  # Remove artifacts older than 7 days
```

## Security Checklist
- [x] Path traversal prevention: `ensure_within_boundary()` validates all artifact reads
- [x] Session scoping: Artifacts accessible only within correct session context
- [x] File permissions: `.liara_artifacts/` owned by API service, not world-accessible
- [x] URL encoding: Artifact paths properly quoted in URLs
- [x] Cache headers: `Cache-Control: private, no-store` on artifact responses
- [x] Logging: No sensitive data logged (file paths sanitized in logs)

## User Documentation

### For Chat Users
> **New Feature**: LIARA can now generate charts in response to data visualization requests!  
> 
> Try asking: "Plot a line chart of the quarterly sales data: Q1: 45, Q2: 58, Q3: 52, Q4: 67"  
> 
> Charts will appear inline in the chat (GTK UI) or as clickable links (Terminal UI).

### For Administrators
> **Monitoring**: Watch `liara.api.artifacts` logs for generation activity.  
> **Configuration**: Use `PLOTTING_TOOLS_ENABLED=true/false` to control feature availability.  
> **Disk Space**: Artifacts stored in `.liara_artifacts/<session_id>/`; auto-cleanup recommended.

## Success Metrics (Post-Rollout)

- **Adoption**: % of chat sessions with at least one plot request
- **Quality**: User satisfaction rating for chart accuracy and appearance
- **Performance**: P95 response latency with artifacts <500ms
- **Reliability**: Artifact generation success rate ≥98%
- **Security**: Zero unauthorized cross-session artifact access incidents

## Next Steps

1. ✅ Confirm all tests passing
2. ✅ Verify logging instrumentation
3. ⏳ Deploy to internal testing environment
4. ⏳ Gather feedback and iterate
5. ⏳ Deploy to staging (Phase 2)
6. ⏳ Deploy to production (Phase 3)

---

**Prepared**: April 21, 2026  
**Feature Status**: Production-Ready  
**Last Updated**: Rollout Planning
