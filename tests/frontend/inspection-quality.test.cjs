// Uses an existing TypeScript installation; no dependency install or app startup.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require(process.env.LIARA_TYPESCRIPT_PATH || 'typescript');
const filename = path.resolve(__dirname, '../../frontend/web-ui/src/app/architecture/inspection-quality.ts');
const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  reportDiagnostics: true,
});
assert.equal(output.diagnostics.length, 0);
const exportsObject = {};
vm.runInNewContext(output.outputText, { exports: exportsObject });
const { inspectionEvidence } = exportsObject;

function input(tail) {
  return {
    mode: 'observe', action: 'completed', job_state: 'completed', exit_code: tail === 'error' ? 1 : 0,
    quality_verdict: tail === 'error' ? 'failed' : tail === 'warning' ? 'attention' : 'passed',
    findings_total: tail ? 101 : 100,
    highest_severity: tail || 'info',
    severity_counts: { info: 100, warning: +(tail === 'warning'), error: +(tail === 'error') },
    disposition_counts: { baseline: 100, windows_mount: 0, new: +(!!tail) },
    findings: Array.from({ length: 100 }, () => ({ severity: 'info' })),
  };
}

for (const [tail, verdict] of [['error', 'failed'], ['warning', 'attention'], [null, 'passed']]) {
  test(`separate completed execution from ${verdict} quality`, () => {
    const [execution, quality] = inspectionEvidence(input(tail));
    assert.equal(execution.label, 'Prüfausführung');
    assert.equal(execution.value, 'observe:completed');
    assert.equal(quality.label, 'Assurance-Qualität');
    assert.equal(quality.value, verdict);
    assert.equal(quality.state, verdict === 'passed' ? 'healthy' : 'attention');
    assert.match(quality.detail, tail ? /100 von 101/ : /100 von 100/);
    assert.match(quality.detail, /akzeptiert: 100/);
  });
}

test('legacy completed record without quality stays unknown', () => {
  const [, quality] = inspectionEvidence({ job_state: 'completed', findings: [] });
  assert.equal(quality.value, 'unknown');
  assert.equal(quality.state, 'attention');
});

test('nonzero exit and inconsistent data never render passed', () => {
  for (const override of [{ exit_code: 1 }, { findings_total: 0 }, { quality_verdict: null },
      { highest_severity: null }, { disposition_counts: {} }]) {
    const [, quality] = inspectionEvidence({ ...input(null), ...override });
    assert.notEqual(quality.value, 'passed');
    assert.equal(quality.state, 'attention');
  }
});
