'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../../src/sat_sim/web/satellite-scene.js'), 'utf8');
const start = source.indexOf('function createStrictTelemetryTimeJoin(');
const end = source.indexOf('\nfunction ', start + 10);
assert.ok(start >= 0 && end > start, 'strict time join must be implemented in the scene module');
const context = vm.createContext({});
vm.runInContext(source.slice(start, end) + '\nglobalThis.createStrictTelemetryTimeJoin = createStrictTelemetryTimeJoin;', context);
const createJoin = context.createStrictTelemetryTimeJoin;
const meta = {sessionId:'session-A', epochUtc:'2026-01-01T00:00:00Z', frame:'N_INERTIAL', timeSystem:'UTC',
  orbitStreamId:'whole.orbit', adcsStreamId:'whole.adcs', maxPending:2};
function frame(stream, sequence, time, quality='GOOD', session='session-A') {
  return {stream, sequence, sim_time_s:time, quality, session_id:session, values:{sample:sequence}};
}
test('pairs orbit and ADCS frames only at exact simulation time in either arrival order', () => {
  for (const reverse of [false,true]) {
    const join=createJoin(meta);
    const a=frame('whole.orbit',0,1.0), b=frame('whole.adcs',0,1.0);
    const first=reverse?join.accept('adcs',b,meta):join.accept('orbit',a,meta);
    assert.equal(first,null);
    const pair=reverse?join.accept('orbit',a,meta):join.accept('adcs',b,meta);
    assert.equal(pair.sim_time_s,1.0);
    assert.equal(pair.orbit,a);
    assert.equal(pair.adcs,b);
  }
});
test('does not nearest-neighbor match different floating simulation times', () => {
  const join=createJoin(meta);
  assert.equal(join.accept('orbit',frame('whole.orbit',0,1.0),meta),null);
  assert.equal(join.accept('adcs',frame('whole.adcs',0,1.000000001),meta),null);
  assert.equal(join.pendingCount(),2);
});
test('rejects wrong session, epoch, reference frame and time system', () => {
  const join=createJoin(meta);
  assert.equal(join.accept('orbit',frame('whole.orbit',0,1,'GOOD','other'),meta),null);
  for (const patch of [{epochUtc:'2026-01-02T00:00:00Z'},{frame:'BODY'},{timeSystem:'TAI'}]) {
    assert.equal(join.accept('adcs',frame('whole.adcs',0,1),{...meta,...patch}),null);
  }
  assert.equal(join.pendingCount(),0);
});
test('reports sequence gaps independently for both streams', () => {
  const join=createJoin(meta);
  join.accept('orbit',frame('whole.orbit',0,1),meta);
  join.accept('adcs',frame('whole.adcs',0,1),meta);
  join.accept('orbit',frame('whole.orbit',2,3),meta);
  const pair=join.accept('adcs',frame('whole.adcs',1,3),meta);
  assert.equal(pair.orbit_sequence_gap,true);
  assert.equal(pair.adcs_sequence_gap,false);
  join.accept('orbit',frame('whole.orbit',3,4),meta);
  const next=join.accept('adcs',frame('whole.adcs',3,4),meta);
  assert.equal(next.orbit_sequence_gap,false);
  assert.equal(next.adcs_sequence_gap,true);
});
test('rejects non-GOOD quality, duplicate sequence and out-of-order frames', () => {
  const join=createJoin(meta);
  assert.equal(join.accept('orbit',frame('whole.orbit',0,1,'INVALID'),meta),null);
  assert.equal(join.accept('orbit',frame('whole.orbit',1,1),meta),null);
  assert.equal(join.accept('orbit',frame('whole.orbit',1,2),meta),null);
  assert.equal(join.accept('orbit',frame('whole.orbit',0,3),meta),null);
  assert.equal(join.pendingCount(),1);
});
test('bounds unmatched frame cache and evicts oldest unmatched simulation times', () => {
  const join=createJoin(meta);
  for (let i=0;i<4;i++) join.accept('orbit',frame('whole.orbit',i,i+1),meta);
  assert.equal(join.pendingCount(),2);
  assert.equal(join.accept('adcs',frame('whole.adcs',0,1),meta),null);
  assert.equal(join.pendingCount(),3); // two orbit entries plus the unmatched ADCS entry
});
test('zero-valued ADCS measurement is valid when frame quality is GOOD', () => {
  const join=createJoin(meta);
  const a=frame('whole.orbit',0,0), b=frame('whole.adcs',0,0);
  b.values.sample=0;
  join.accept('orbit',a,meta);
  const pair=join.accept('adcs',b,meta);
  assert.equal(pair.adcs.values.sample,0);
});

const appSource = fs.readFileSync(path.join(__dirname, '../../src/sat_sim/web/app.js'), 'utf8');
const adcsStart = appSource.indexOf('function sceneAdcsStream(');
const adcsEnd = appSource.indexOf('\n  function setSceneAdcsJoinStatus', adcsStart);
assert.ok(adcsStart >= 0 && adcsEnd > adcsStart, 'ADCS stream selector must exist');
const adcsSelectorContext = vm.createContext({ADCS_SCENE_FIELDS:['adcs.rw.speed_rad_s_0','adcs.rw.speed_rad_s_1','adcs.rw.speed_rad_s_2']});
vm.runInContext(appSource.slice(adcsStart, adcsEnd)
  + '\nglobalThis.sceneAdcsStream = sceneAdcsStream;', adcsSelectorContext);
const selectAdcsStream = adcsSelectorContext.sceneAdcsStream;
test('selects the dedicated whole.adcs quality domain, never the INVALID whole.fast superset', () => {
  const catalog = [
    {stream_id:'whole.fast', fields:['adcs.rw.speed_rad_s_0','adcs.rw.speed_rad_s_1','adcs.rw.speed_rad_s_2']},
    {stream_id:'whole.adcs', fields:['adcs.rw.speed_rad_s_0','adcs.rw.speed_rad_s_1','adcs.rw.speed_rad_s_2']},
    {stream_id:'whole.orbit', fields:['orbit.position_m_x']},
  ];
  assert.equal(selectAdcsStream({telemetry_catalog:catalog}).stream_id, 'whole.adcs');
});
test('does not fall back to a mixed-quality stream when dedicated whole.adcs is absent', () => {
  const catalog = [{stream_id:'whole.fast', fields:['adcs.rw.speed_rad_s_0','adcs.rw.speed_rad_s_1','adcs.rw.speed_rad_s_2']}];
  assert.equal(selectAdcsStream({telemetry_catalog:catalog}), null);
});

const subscriptionStart = appSource.indexOf('function interactiveSubscriptionStreams(');
const subscriptionEnd = appSource.indexOf('\n  function connectInteractiveSocket', subscriptionStart);
assert.ok(subscriptionStart >= 0 && subscriptionEnd > subscriptionStart,
  'scene telemetry subscription helper must exist');
const subscriptionContext = vm.createContext({});
vm.runInContext(appSource.slice(subscriptionStart, subscriptionEnd)
  + '\nglobalThis.interactiveSubscriptionStreams = interactiveSubscriptionStreams;', subscriptionContext);
const sceneSubscriptionStreams = subscriptionContext.interactiveSubscriptionStreams;
test('always subscribes dedicated orbit and ADCS streams for whole-spacecraft scene independently of chart selection', () => {
  const session = {capability_id:'whole_spacecraft.unified_native.v1', telemetry_catalog:[
    {stream_id:'whole.fast'}, {stream_id:'whole.orbit'}, {stream_id:'whole.adcs'},
  ]};
  const streams = sceneSubscriptionStreams(session, ['whole.fast']);
  assert.deepEqual(Array.from(streams).sort(), ['whole.adcs','whole.fast','whole.orbit']);
});
test('does not invent missing scene streams or substitute whole.fast', () => {
  const session = {capability_id:'whole_spacecraft.unified_native.v1', telemetry_catalog:[
    {stream_id:'whole.fast'}, {stream_id:'whole.orbit'},
  ]};
  assert.deepEqual(Array.from(sceneSubscriptionStreams(session, ['whole.fast'])), ['whole.fast','whole.orbit']);
});

const continuousSource = fs.readFileSync(path.join(__dirname, '../../src/sat_sim/web/continuous.js'), 'utf8');
test('continuous console independently polls whole.adcs and joins it with the whole.orbit scene stream', () => {
  assert.match(continuousSource, /sceneAdcsTelemetryRequest/);
  assert.match(continuousSource, /state\.sceneAdcsStream/);
  assert.match(continuousSource, /sceneTelemetryJoin\.accept\('orbit', frame, joinMeta\)/);
  assert.match(continuousSource, /sceneTelemetryJoin\.accept\('adcs', frame, joinMeta\)/);
  assert.match(continuousSource, /updateAdcsTelemetry\(pair/);
  assert.match(continuousSource, /whole\.adcs/);
});
