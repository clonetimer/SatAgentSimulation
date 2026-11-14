'use strict';
// Only Globe/DOM are controlled here; the production bridge and projection run unchanged.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const web = path.join(__dirname, '../../src/sat_sim/web');

function harness() {
  const status = {textContent:'', dataset:{}};
  const host = {clientWidth:1200, clientHeight:800, replaceChildren(){}};
  const data = {};
  let ready;
  const controls = {addEventListener(){}, removeEventListener(){}};
  const proxy = new Proxy({}, {get(_, key) {
    if (key === 'controls') return () => controls;
    if (key === 'renderer') return () => ({forceContextLoss(){},domElement:{remove(){}}});
    if (key === 'getGlobeRadius') return () => 100;
    if (key === 'pointOfView') return v => v || {lat:0,lng:0,altitude:2};
    return (...args) => {if (key === 'onGlobeReady') ready = args[0]; data[key] = args[0]; return proxy;};
  }});
  const window = {Globe: function(){return proxy;}};
  const context = vm.createContext({window, console, queueMicrotask,
    THREE:{MathUtils:{degToRad:x=>x*Math.PI/180,radToDeg:x=>x*180/Math.PI}},
    document:{getElementById:id=>id==='satelliteSceneCanvas'?host:status,querySelectorAll:()=>[],addEventListener(){}},
    ResizeObserver:class {observe(){} disconnect(){}},
    performance:{now:()=>0},requestAnimationFrame:()=>1,cancelAnimationFrame(){},setTimeout:()=>1,clearTimeout(){}});
  const projection = fs.readFileSync(path.join(web,'realtime-orbit.mjs'),'utf8').replace(/^export /gm,'');
  vm.runInContext(projection,context);
  const source = fs.readFileSync(path.join(web,'satellite-scene.js'),'utf8');
  const imports = source.match(/^import[^\n]*\n/gm) || [];
  assert.equal(imports.length,2,'update the explicit harness loader for additional imports');
  vm.runInContext(source.replace(/^import[^\n]*\n/gm,''),context);
  return {api:window.SatScene,data,status,async ready(){ready();await Promise.resolve();await Promise.resolve();}};
}
const metadata = {sessionId:'native-session',mode:'bounded',epochUtc:'2000-01-01T12:00:00Z',timeSystem:'UTC',positionFrame:'J2000_INERTIAL',streamId:'orbit'};
function payload(sequence=1, simTime=0) {return {sessionId:'native-session',frame:{session_id:'native-session',stream:'orbit',sequence,sim_time_s:simTime,quality:'GOOD',values:{'orbit.position_m_x':7000000,'orbit.position_m_y':0,'orbit.position_m_z':0}}};}

test('default bounded spacecraft streams actually include all three native position fields',()=>{
  const source=fs.readFileSync(path.join(web,'app.js'),'utf8');
  const a=source.indexOf('const INTERACTIVE_CAPABILITIES = {');
  const b=source.indexOf('const INTERACTIVE_TARGET_LABELS',a);
  assert.ok(a>=0 && b>a);
  const catalog=vm.runInNewContext(source.slice(a,b)+'; INTERACTIVE_CAPABILITIES;');
  const descriptor=catalog['whole_spacecraft.unified_native.v1'];
  const fields=descriptor.telemetryGroups.flatMap(group=>group.fields);
  assert.ok(['x','y','z'].every(axis=>fields.includes('orbit.position_m_'+axis)), 'P9F_DEFAULT_BOUNDED_POSITION_NOT_PUBLISHED');
});

test('live satellite and trail use the configured Globe height accessor',async()=>{
  const h=harness();h.api.setRealtimeSession(metadata);await h.api.enter();await h.ready();
  assert.equal(h.api.updateRealtimeFrame(payload()),true);
  const object=h.data.objectsData[0],point=h.data.pathsData[0].points[0];
  assert.ok(Number.isFinite(object[h.data.objectAltitude]),'P9F_LIVE_OBJECT_ALTITUDE_NOT_BOUND');
  assert.ok(Number.isFinite(point[h.data.pathPointAlt]),'P9F_LIVE_TRAIL_ALTITUDE_NOT_BOUND');
  assert.ok(Math.abs(object[h.data.objectAltitude]-(7000000-6378137)/6378137)<1e-12);
});

test('a higher sequence cannot move simulation time backwards',async()=>{
  const h=harness();h.api.setRealtimeSession(metadata);await h.api.enter();await h.ready();
  assert.equal(h.api.updateRealtimeFrame(payload(1,20)),true);
  assert.equal(h.api.updateRealtimeFrame(payload(2,10)),false,'P9F_SIM_TIME_ROLLBACK_ACCEPTED');
});

test('missing position frame is not silently assumed',async()=>{
  const h=harness();const incomplete={...metadata};delete incomplete.positionFrame;
  h.api.setRealtimeSession(incomplete);await h.api.enter();await h.ready();
  assert.equal(h.api.updateRealtimeFrame(payload()),false,'P9F_MISSING_FRAME_DEFAULTED');
});

test('an older pre-ready frame cannot replace the newest received frame',async()=>{
  const h=harness();h.api.setRealtimeSession(metadata);await h.api.enter();
  h.api.updateRealtimeFrame(payload(8,8));h.api.updateRealtimeFrame(payload(7,7));await h.ready();
  assert.match(h.status.textContent,/8\.00/,'P9F_PENDING_FRAME_REGRESSED');
});
