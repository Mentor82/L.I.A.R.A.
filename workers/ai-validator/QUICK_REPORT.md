# Quick report v2

Quick is a static check, not proof of runtime correctness or a full security audit.
No project modules are imported. Sources are mounted read-only; Ruff cache and
TypeScript emission/incremental output are disabled. Checker versions are recorded.

## Execution and quality

The shared `quick_policy.py` module is mounted with the runner and used by the
host parser. A supported, fresh, consistent report means execution `completed`,
even when its quality verdict is `failed`. Missing reports, malformed schemas,
unavailable/incompatible reports, exit contradictions, changed workspaces and
attempt mismatches cannot produce a passing assurance result.

| Effective findings | Report exit | Quality / assurance |
| --- | --- | --- |
| none, or accepted baseline/mount info | 0 | passed |
| new warning | 0 | attention |
| new warning with `strict_mode=true` | 1 | failed |
| syntax, functional/security or checker error | 1 | failed |

Existing assurance evaluates these effective severities and exit code. Existing
Observer rules remain intact: attention/failed assurance does not qualify as
quiet. We do not make the Observer green by hiding new warnings. Detailed
findings preserve original severity and `new`, `baseline`, or `windows_mount`
disposition in the report and service finding metadata.

Each invocation (including retry of one job ID) gets a new `attempt-<uuid>` output
directory. Reports bind job ID, attempt ID, source snapshot digest and runner and
policy digests. The host verifies workspace stability again after execution.
These hashes bind execution artifacts; they are not signatures or authentication.

## Baseline

`quick-baseline.json` is reviewed source, not runtime configuration. It records a
reference commit, source report SHA-256, configuration digest, checker versions
and a narrow explicit cosmetic-rule selection. Initial entries derive from the
previous operator Canary report (job `0938823e-e6b3-46ca-8970-ecf3cc45ab53`) at
reference `536938b3a533869e0610d8b10f945fb7c4a6baa3`. Only paths tracked at that
reference and explicit cosmetic rules were selected: 2,356 unique fingerprints,
2,684 occurrences. This does not accept all 4,281 old findings. Existing functional
and security diagnostics still require review and can keep assurance failed.

Fingerprints include normalized relative path, rule and adjacent stripped code
lines. They survive simple line-number movement; occurrence budgets prevent an
additional identical diagnostic from inheriting acceptance. Changed surrounding
code is conservatively treated as new. Removed findings no longer affect verdict.
No normal scan writes or extends the baseline. Updating it requires a separate
reviewed source change and refreshed provenance. Configuration/checker drift
disables baseline acceptance and is reported as `baseline_incompatible`.

## Windows mount exception

Only EXE002 can be informational when all evidence agrees: the host is Windows,
the workspace is a direct Windows-drive bind rather than WSL staging, Git stage
zero records mode `100644` for this exact path, and the container observes execute
bits. Unknown/untracked paths, mode `100755`, Linux, WSL staging, or missing mode
evidence do not qualify. The launcher records this context per attempt. Other
rules are unchanged; an exception is never inferred from container OS alone.

## Validation

Run from an isolated checkout using the existing Python environment; a test-only
`PERSISTENCE_POLICY=development` avoids needing production storage credentials:

```powershell
$env:PERSISTENCE_POLICY = 'development'
python -m pytest tests/unit/test_quick_report_policy.py tests/unit/test_validator_quick_findings.py tests/unit/test_validator_job_recovery.py tests/unit/test_self_observer.py tests/unit/test_memory_service_app.py -k 'not test_backed_store_persists_staging_and_proposals' -q
```

119 passed, 1 deselected at this revision. The deselected test fails on the PR's
unchanged base due to missing `_relation_fallback_store`; its separate fix is not
included here. Lease tests now stub execution, test both terminal outcomes, retain
shutdown recovery coverage, and explicitly verify that an old fenced worker cannot
overwrite a newer result. Schema tests cover the original `{}` false-green,
unknown versions/counts/checks, severity tampering, stale attempts and process exit
contradictions. Baseline and platform evidence tests cover positive/negative cases.

Container integration was run through the real host executor using the existing
`lab-web/ai-validator:latest` test image (Ruff 0.16.2, ShellCheck 0.10.0, TypeScript
7.0.2): clean Python -> completed/passed; new F401 -> completed/attention;
Python syntax error -> completed/failed. Source hashes stayed unchanged and no
`__pycache__` appeared. Reports/logs are in the review worktree's ignored
`artifacts/validator_jobs/container-*/attempt-*/` directories. No production
services were restarted and no deployment was performed.
