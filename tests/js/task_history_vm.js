/* P7-A contract tests against the exact task-history functions from app.js.
 * These are DOM-independent behavioral checks, not a Chromium acceptance run. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../src/sat_sim/web/app.js'), 'utf8');
function section(start, end) {
  const first = source.indexOf(start);
  const last = source.indexOf(end, first);
  assert.ok(first >= 0 && last > first, `Expected source function boundary: ${start}`);
  return source.slice(first, last);
}

const code = section('  function taskRunFailureExplanation(', '  function updateTaskHistorySelection(');
const context = vm.createContext({});
vm.runInContext(code, context);
const explain = (run, execution, bundle) => context.taskRunFailureExplanation(run, execution, bundle);
const eligible = (pair) => context.taskRunComparisonEligibility(pair);

const good = (id) => ({
  run_id: id, status: 'SUCCEEDED', sealed: true,
  primary_capability_id: 'whole_spacecraft.unified_native.v1',
});
assert.equal(eligible([good('a'), good('b')]).ok, true);
assert.equal(eligible([good('a')]).ok, false);
assert.equal(eligible([good('a'), { ...good('b'), status: 'FAILED' }]).ok, false);
assert.equal(eligible([good('a'), { ...good('b'), sealed: false }]).ok, false);
assert.equal(eligible([good('a'), { ...good('b'), primary_capability_id: 'component.battery.v1' }]).ok, false);
assert.equal(eligible([good('a'), { ...good('a') }]).ok, false);

const failed = explain(
  { run_id: 'failure-a', status: 'FAILED', validation_result: 'NOT_EVALUATED' },
  { state: 'FAILED', error: 'BASILISK_RUNTIME_ERROR' },
  { run_record: { execution_status: 'FAILED', terminal_reason_code: 'NATIVE_EXECUTION_FAILED' }, validation_outcome: null },
);
assert.match(failed, /执行/);
assert.match(failed, /NATIVE_EXECUTION_FAILED/);
assert.match(failed, /BASILISK_RUNTIME_ERROR/);
assert.doesNotMatch(failed, /验收通过/);

const validation = explain(
  { run_id: 'invalid-a', status: 'SUCCEEDED', validation_result: 'FAIL' },
  { state: 'SUCCEEDED' },
  { run_record: { status: 'SUCCEEDED', validation_status: 'FAIL' }, validation_outcome: { result: 'FAIL', reason_code: 'ASSERTION_FAILED' } },
);
assert.match(validation, /执行已完成/);
assert.match(validation, /验收未通过/);
assert.match(validation, /ASSERTION_FAILED/);

const timeout = explain({ status: 'TIMED_OUT' }, { state: 'TIMED_OUT' }, null);
assert.match(timeout, /超时/);
assert.match(timeout, /未提供/);

console.log('P7-A task-history explanations and comparison prechecks passed');
