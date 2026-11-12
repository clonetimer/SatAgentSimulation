'use strict';
// Execute the actual production functions, not a second implementation.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const test = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../../src/sat_sim/web/app.js'), 'utf8');
function section(start, end) {
  const first = source.indexOf(start);
  const last = source.indexOf(end, first);
  assert.ok(first >= 0 && last > first, 'Production source boundary missing: ' + start);
  return source.slice(first, last);
}
function harness() {
  const state = {
    activeView: 'wizard', activeTaskCenterId: 'editing-A', activeTaskVersion: 3,
    activeTaskVersionTaskId: 'editing-A', activeRunId: 'loaded-A',
    sharedWorkbenchContext: {sourceView:null, sourceLabel:null},
    formData: {duration_s: 17}, taskSpec: {task: {id: 'spec-A'}},
  };
  const messages = [];
  const c = vm.createContext({state, document: {querySelectorAll: () => []},
    wizardMessage: (role, message) => messages.push(message), renderSimulationWizard: () => {},
  });
  vm.runInContext(section('  const SHARED_CONTEXT_VIEWS', '  function switchView(name)'), c);
  c.switchView = (name) => { c.noteSharedContextTransition(name); state.activeView = name; };
  return {c, state, messages};
}
function identity(s) { return JSON.stringify([s.activeTaskCenterId, s.activeTaskVersion, s.activeRunId, s.taskSpec, s.formData]); }

test('reference updates never retarget the editor or the running selection', () => {
  const {c,state} = harness(); const before = identity(state);
  c.updateSharedWorkbenchContext({taskId:'inspected-B', taskVersion:9, runId:'inspected-B'});
  assert.equal(identity(state), before);
});
test('snapshot uses live editor identity, not stale result reference identity', () => {
  const {c,state} = harness();
  state.sharedWorkbenchContext = {taskId:'stale-B', taskVersion:9, runId:'stale-B', bundleId:'stale-B'};
  const value = c.sharedContextSnapshot();
  assert.equal(value.taskId, 'editing-A'); assert.equal(value.taskVersion, 3);
  assert.equal(value.runId, 'loaded-A'); assert.equal(value.bundleId, null);
});
test('task version is not borrowed from another opened task', () => {
  const {c,state} = harness(); state.activeTaskCenterId = 'editing-new';
  assert.equal(c.sharedContextSnapshot().taskVersion, null);
});
test('specialist chain retains first source and return emits wizard feedback', () => {
  const {c,state,messages} = harness(); const before = identity(state);
  c.switchView('experiments'); c.switchView('goals'); c.switchView('diagnostics');
  assert.equal(state.sharedWorkbenchContext.sourceView, 'wizard');
  c.returnToSharedContextSource();
  assert.equal(state.activeView, 'wizard'); assert.equal(state.sharedWorkbenchContext.sourceView, null);
  assert.equal(identity(state), before);
  assert.ok(messages.some(message => message.includes('当前配置、任务版本和已有运行记录保持不变')));
});
test('ordinary navigation back clears source before a fresh creator visit', () => {
  const {c,state} = harness();
  c.switchView('experiments'); c.switchView('wizard');
  assert.equal(state.sharedWorkbenchContext.sourceView, null);
  c.switchView('creator'); c.switchView('runs');
  assert.equal(state.sharedWorkbenchContext.sourceView, 'wizard');
  c.returnToSharedContextSource(); assert.equal(state.activeView, 'wizard');
});
test('same-view navigation does not create a return loop', () => {
  const {c,state} = harness(); state.activeView = 'experiments';
  c.switchView('experiments'); assert.equal(state.sharedWorkbenchContext.sourceView, null);
});
test('cancelled realtime departure does not change the source reference', () => {
  const {c,state} = harness(); state.activeView = 'continuous';
  const before = JSON.stringify(state);
  c.window = {confirm: () => false}; c.wizardRealtimeSessionActive = () => true;
  vm.runInContext(section('  function switchView(name)', '    state.activeView = name;') + '  }', c);
  c.switchView('experiments'); assert.equal(JSON.stringify(state), before);
});
test('clearing a reference never clears the live run controller', () => {
  const {c,state} = harness(); c.updateSharedWorkbenchContext({runId:null,bundleId:null});
  assert.equal(state.activeRunId, 'loaded-A');
});

test('experiment source survives diagnostics to full creator Run detail', () => {
  const {c,state} = harness();
  state.activeView = 'experiments';
  state.sharedWorkbenchContext = {sourceView:'experiments', sourceLabel:'实验中心'};
  c.switchView('diagnostics'); c.switchView('creator');
  assert.equal(state.sharedWorkbenchContext.sourceView, 'experiments');
  c.returnToSharedContextSource(); assert.equal(state.activeView, 'experiments');
});
