'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../../src/sat_sim/web/satellite-scene.js'), 'utf8');
const start = source.indexOf('function advanceWheelPhaseState(');
const end = source.indexOf('\nfunction updateAdcsTelemetry', start);
assert.ok(start >= 0 && end > start, 'persistent wheel phase integrator must exist');
const context = vm.createContext({});
vm.runInContext(source.slice(start, end) + '\nglobalThis.advanceWheelPhaseState = advanceWheelPhaseState;', context);
const advance = context.advanceWheelPhaseState;

test('integrates variable wheel speed with trapezoidal time integration', () => {
  const first = advance(null, {sourceKey:'session|epoch|whole.adcs',simTimeS:0,speed:100});
  const second = advance(first, {sourceKey:'session|epoch|whole.adcs',simTimeS:2,speed:200});
  assert.equal(first.phaseRad, 0);
  assert.equal(second.phaseRad, 300);
  assert.equal(second.valid, true);
});

test('sequence discontinuity rebases phase and never integrates across the gap', () => {
  const first = advance(null, {sourceKey:'s',simTimeS:0,speed:100});
  const gap = advance(first, {sourceKey:'s',simTimeS:2,speed:200,discontinuity:true});
  assert.equal(gap.phaseRad, 0);
  assert.equal(gap.reason, 'relative-rebase');
  const next = advance(gap, {sourceKey:'s',simTimeS:4,speed:200});
  assert.equal(next.phaseRad, 400);
});

test('missing speed invalidates phase and the next sample starts a new relative segment', () => {
  const first = advance(null, {sourceKey:'s',simTimeS:0,speed:20});
  const missing = advance(first, {sourceKey:'s',simTimeS:1,speed:null});
  assert.equal(missing.valid, false);
  assert.equal(missing.phaseRad, null);
  const recovered = advance(missing, {sourceKey:'s',simTimeS:3,speed:50});
  assert.equal(recovered.phaseRad, 0);
  const next = advance(recovered, {sourceKey:'s',simTimeS:4,speed:50});
  assert.equal(next.phaseRad, 50);
});

test('session, epoch or stream changes reset the relative phase origin', () => {
  const first = advance(null, {sourceKey:'session-A|epoch|whole.adcs',simTimeS:0,speed:10});
  const changed = advance(first, {sourceKey:'session-B|epoch|whole.adcs',simTimeS:10,speed:30});
  assert.equal(changed.phaseRad, 0);
  assert.equal(changed.sourceKey, 'session-B|epoch|whole.adcs');
});

test('phase accumulation is independent of the 600-frame display trail limit', () => {
  let phase = null;
  for (let index = 0; index <= 601; index += 1) {
    phase = advance(phase, {sourceKey:'s',simTimeS:index,speed:1});
  }
  assert.equal(phase.phaseRad, 601);
});

test('production wheel rendering reads accumulated phase state rather than recomputing from telemetryTrail', () => {
  const start = source.indexOf('function integrateWheelPhaseFromTelemetry(');
  const end = source.indexOf('\nfunction updateSimulationWheelMotion', start);
  assert.ok(start >= 0 && end > start);
  const implementation = source.slice(start, end);
  assert.match(implementation, /wheelPhases/);
  assert.doesNotMatch(implementation, /telemetryTrail/);
});
