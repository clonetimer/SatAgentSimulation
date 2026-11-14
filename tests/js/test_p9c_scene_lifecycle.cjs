'use strict';
// Exercise the real scene controller with controlled third-party readiness/events.
// Actual WebGL rendering remains covered by test_p9c_satellite_scene.py in CI.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname,
  '../../src/sat_sim/web/satellite-scene.js'), 'utf8');

function harness({ failAt = null } = {}) {
  const frames = new Map(), timers = new Map(), instances = [], observers = [];
  let nextId = 0, now = 0;
  const status = { textContent: '', dataset: {} };
  const warnings = [];
  const host = { clientWidth: 1200, clientHeight: 700, children: [],
    replaceChildren(...children) { this.children = children; } };
  const buttons = ['track', 'free', 'home'].map(action => ({
    dataset: { sceneAction: action }, disabled: false, attributes: {},
    setAttribute(key, value) { this.attributes[key] = value; }
  }));
  class Node {
    constructor() {
      this.children = []; this.rotation = {};
      this.position = { set() {} };
    }
    add(child) { this.children.push(child); }
  }
  class Controls {
    constructor() { this.listeners = new Map(); this.disposed = false; }
    addEventListener(name, fn) { this.listeners.set(name, fn); }
    removeEventListener(name, fn) {
      if (this.listeners.get(name) === fn) this.listeners.delete(name);
    }
    start() { this.listeners.get('start')?.(); }
    dispose() { this.disposed = true; this.listeners.clear(); }
  }
  class Globe {
    constructor(container) {
      const instance = this;
      this.views = []; this.readyCallback = null; this.destroyed = 0;
      this.control = new Controls();
      this.canvas = { remove: () => { container.children = []; } };
      this.render = { domElement: this.canvas, dispose() {}, forceContextLoss() {} };
      container.children.push(this.canvas);
      const proxy = new Proxy(this, {
        get(target, name) {
          if (name in target) {
            const value = target[name];
            return typeof value === 'function' ? value.bind(target) : value;
          }
          if (name === 'objectsData') return (rows) => {
            if (name === failAt) throw new Error(`internal ${name} setup failed`);
            instance.objectRows = rows; return proxy;
          };
          if (name === 'pathsData') return (rows) => {
            if (name === failAt) throw new Error(`internal ${name} setup failed`);
            instance.pathRows = rows; return proxy;
          };
          return (...args) => {
            if (name === failAt) throw new Error(`internal ${name} setup failed`);
            if (name === 'onGlobeReady') instance.readyCallback = args[0];
            return proxy;
          };
        }
      });
      instances.push(this);
      return proxy;
    }
    controls() { return this.control; }
    renderer() { return this.render; }
    getGlobeRadius() { return 100; }
    pointOfView(view, duration) {
      if (view === undefined) return this.views.at(-1)?.view || {lat: 0, lng: 0, altitude: 2};
      this.views.push({view, duration});
    }
    pauseAnimation() {}
    _destructor() { this.destroyed += 1; this.control.dispose(); }
    ready() { this.readyCallback?.(); }
  }
  const window = { Globe };
  const context = vm.createContext({
    THREE: { MathUtils: { degToRad: x => x * Math.PI / 180, radToDeg: x => x * 180 / Math.PI },
      Group: Node, Mesh: Node, BoxGeometry: Node, MeshStandardMaterial: Node,
      CylinderGeometry: Node, ConeGeometry: Node, SphereGeometry: Node, MeshBasicMaterial: Node },
    window,
    document: {
      getElementById: id => id === 'satelliteSceneCanvas' ? host : status,
      querySelectorAll: () => buttons,
      addEventListener() {}
    },
    ResizeObserver: class {
      constructor(fn) { this.fn = fn; this.disconnected = false; observers.push(this); }
      observe() {}
      disconnect() { this.disconnected = true; }
    },
    requestAnimationFrame: fn => { const id = ++nextId; frames.set(id, fn); return id; },
    cancelAnimationFrame: id => frames.delete(id),
    setTimeout: fn => { const id = ++nextId; timers.set(id, fn); return id; },
    clearTimeout: id => timers.delete(id),
    queueMicrotask,
    performance: { now: () => now },
    console: { warn(...args) { warnings.push(args.map(String).join(' ')); }, error() {} }
  });
  vm.runInContext(source.replace(/^import \* as THREE from 'three';\s*/, ''), context,
    {filename: 'satellite-scene.js'});
  return { api: window.SatScene, instances, observers, host, status, frames, timers, buttons, warnings,
    async ready(index = instances.length - 1) {
      if (!instances[index]) throw new Error(`scene not constructed; status=${status.textContent}; warnings=${warnings.join(' | ')}`);
      instances[index].ready(); await Promise.resolve(); await Promise.resolve();
    },
    tick() {
      now += 200;
      const queued = [...frames.values()]; frames.clear(); queued.forEach(fn => fn(now));
    },
    timeout() { const queued = [...timers.values()]; timers.clear(); queued.forEach(fn => fn()); }
  };
}

test('a canvas is not readiness: imagery callback gates status and animation', async () => {
  const h = harness(); await h.api.enter();
  assert.notEqual(h.status.dataset.kind, 'success', 'must not announce success before imagery');
  assert.equal(h.frames.size, 0, 'do not animate an unloaded scene');
  await h.ready();
  assert.equal(h.status.dataset.kind, 'success');
  assert.equal(h.frames.size, 1);
});

test('repeated entry while loading keeps one viewer and one ready animation loop', async () => {
  const h = harness(); await h.api.enter(); await h.api.enter();
  assert.equal(h.instances.length, 1);
  await h.ready(); await h.ready();
  assert.equal(h.frames.size, 1);
});

test('readiness delivered after leaving cannot resurrect the old scene', async () => {
  const h = harness(); await h.api.enter(); h.api.exit();
  const message = h.status.textContent;
  await h.ready(0);
  assert.equal(h.frames.size, 0);
  assert.equal(h.host.children.length, 0);
  assert.equal(h.status.textContent, message);
});

test('a previous viewer readiness cannot mark the replacement viewer ready', async () => {
  const h = harness(); await h.api.enter(); h.api.exit(); await h.api.enter();
  await h.ready(0);
  assert.notEqual(h.status.dataset.kind, 'success');
  assert.equal(h.frames.size, 0);
  await h.ready(1);
  assert.equal(h.status.dataset.kind, 'success');
  assert.equal(h.frames.size, 1);
});

test('manual camera interaction releases tracking without the next tick reclaiming it', async () => {
  const h = harness(); await h.api.enter(); await h.ready(); h.api.track();
  h.tick(); const globe = h.instances[0];
  globe.control.start();
  const viewsAfterDragStart = globe.views.length;
  h.tick();
  assert.equal(globe.views.length, viewsAfterDragStart, 'camera must remain under user control');
});

test('loading timeout reports a recoverable error and cancels scene work', async () => {
  const h = harness(); await h.api.enter(); h.timeout();
  assert.equal(h.status.dataset.kind, 'error');
  assert.match(h.status.textContent, /重试/);
  assert.equal(h.frames.size, 0);
  assert.equal(h.host.children.length, 0);
  await h.ready();
  assert.equal(h.status.dataset.kind, 'error', 'late imagery must not reverse a timeout');
});

test('failure after renderer construction releases that partially configured instance', async () => {
  const h = harness({failAt: 'pathsData'}); await h.api.enter();
  assert.equal(h.status.dataset.kind, 'error');
  assert.equal(h.instances[0].destroyed, 1);
  assert.equal(h.host.children.length, 0);
  assert.doesNotMatch(h.status.textContent, /internal pathsData/, 'do not expose developer errors');
});

test('exit invokes component teardown exactly once and detaches owned callbacks', async () => {
  const h = harness(); await h.api.enter(); await h.ready();
  const globe = h.instances[0]; h.api.exit(); h.api.exit();
  assert.equal(globe.destroyed, 1, 'the wrapper destructor owns its internal resources');
  assert.equal(globe.control.listeners.size, 0);
  assert.equal(h.frames.size, 0);
  assert.equal(h.timers.size, 0);
  assert.ok(h.observers.every(item => item.disconnected));
});

test('repeated complete enter/exit cycles leave no owned animation or timers', async () => {
  const h = harness();
  for (let i = 0; i < 12; i += 1) {
    await h.api.enter(); await h.ready(); h.api.track(); h.tick(); h.api.exit();
    assert.equal(h.frames.size, 0); assert.equal(h.timers.size, 0);
    assert.equal(h.host.children.length, 0);
  }
  assert.equal(h.instances.length, 12);
  assert.ok(h.instances.every(globe => globe.destroyed === 1));
});

test('initial overview faces the demonstration satellite instead of its far hemisphere', async () => {
  const h = harness(); await h.api.enter(); await h.ready();
  const view = h.instances[0].views[0].view;
  assert.ok(Math.abs(view.lng) <= 30, 'initial satellite longitude is zero; overview must include it');
  assert.ok(Math.abs(view.lat) <= 30);
});

test('returning to overview retains the current satellite in the near hemisphere', async () => {
  const h = harness(); await h.api.enter(); await h.ready(); h.api.track();
  h.tick();
  const globe = h.instances[0];
  const satelliteView = globe.views.at(-1).view;
  h.api.home();
  const overview = globe.views.at(-1).view;
  assert.ok(Math.abs(overview.lng - satelliteView.lng) <= 30);
  assert.ok(Math.abs(overview.lat - satelliteView.lat) <= 30);
  assert.ok(overview.altitude > satelliteView.altitude, 'overview must zoom out');
});


test('ECI/N inertial position converts to WGS-84 geodetic coordinates using the simulation epoch', () => {
  const h = harness();
  const point = h.api.eciToGlobePoint([7000000, 0, 0], '2026-01-01T00:00:00Z', 0);
  assert.ok(point);
  assert.ok(Math.abs(point.lat) < 1e-8);
  assert.ok(Math.abs(point.altitude_km - (7000000 - 6378137) / 1000) < 1e-5);
  assert.ok(point.lng >= -180 && point.lng <= 180);
});

test('Earth rotation is applied from simulation time, not browser wall clock', () => {
  const h = harness();
  const epoch = '2026-01-01T00:00:00Z';
  const start = h.api.eciToGlobePoint([7000000, 0, 0], epoch, 0);
  const sixHours = h.api.eciToGlobePoint([7000000, 0, 0], epoch, 21600);
  let delta = sixHours.lng - start.lng;
  while (delta > 180) delta -= 360;
  while (delta < -180) delta += 360;
  assert.ok(delta < -90 && delta > -91, `expected ~-90.25 degrees, received ${delta}`);
});

test('real Basilisk telemetry replaces the demo orbit and draws a bounded ground track', async () => {
  const h = harness(); await h.api.enter(); await h.ready();
  const epochUtc = '2026-01-01T00:00:00Z';
  assert.match(h.status.textContent, /演示/);
  assert.equal(h.api.updateTelemetry({
    session_id: 'live-a', sequence: 1, sim_time_s: 0,
    values: {'orbit.position_m_x': 7000000, 'orbit.position_m_y': 0, 'orbit.position_m_z': 0}
  }, {epochUtc, frame: 'N_INERTIAL', sourceId: 'live-a', sourceLabel: '实时 Basilisk 会话'}), true);
  assert.equal(h.api.updateTelemetry({
    session_id: 'live-a', sequence: 2, sim_time_s: 10,
    values: {'orbit.position_m_x': 6999000, 'orbit.position_m_y': 100000, 'orbit.position_m_z': 50000}
  }, {epochUtc, frame: 'N_INERTIAL', sourceId: 'live-a', sourceLabel: '实时 Basilisk 会话'}), true);
  const globe = h.instances[0];
  assert.equal(globe.objectRows[0].name, '任务卫星（真实 Basilisk 遥测）');
  assert.equal(globe.objectRows[0].sim_time_s, 10);
  assert.equal(globe.pathRows.length, 1);
  assert.equal(globe.pathRows[0].points.length, 2);
  assert.match(h.status.textContent, /实时 Basilisk 会话/);
  assert.doesNotMatch(h.status.textContent, /演示/);
  assert.equal(h.frames.size, 0, 'real telemetry must stop the demo animation loop');
});

test('switching telemetry sources clears the previous session ground track', async () => {
  const h = harness(); await h.api.enter(); await h.ready();
  const frame = (session_id, sim_time_s, y) => ({
    session_id, sim_time_s,
    values: {'orbit.position_m_x': 7000000, 'orbit.position_m_y': y, 'orbit.position_m_z': 0}
  });
  h.api.updateTelemetry(frame('live-a', 10, 1000), {epochUtc:'2026-01-01T00:00:00Z',frame:'N_INERTIAL',sourceId:'live-a'});
  h.api.updateTelemetry(frame('live-a', 20, 2000), {epochUtc:'2026-01-01T00:00:00Z',frame:'N_INERTIAL',sourceId:'live-a'});
  assert.equal(h.instances[0].pathRows[0].points.length, 2);
  h.api.updateTelemetry(frame('live-b', 0, 0), {epochUtc:'2026-01-01T00:00:00Z',frame:'N_INERTIAL',sourceId:'live-b'});
  assert.equal(h.instances[0].pathRows.length, 0);
  assert.equal(h.instances[0].objectRows[0].sim_time_s, 0);
});

test('invalid epoch, non-finite position and unverified Run never become real scene telemetry', async () => {
  const h = harness(); await h.api.enter(); await h.ready();
  assert.equal(h.api.updateTelemetry({
    sim_time_s: 0,
    values: {'orbit.position_m_x': 7000000, 'orbit.position_m_y': 0, 'orbit.position_m_z': 0}
  }, {epochUtc:'not-an-epoch',frame:'N_INERTIAL',sourceId:'invalid'}), false);
  assert.equal(h.api.updateTelemetry({
    sim_time_s: 0,
    values: {'orbit.position_m_x': Infinity, 'orbit.position_m_y': 0, 'orbit.position_m_z': 0}
  }, {epochUtc:'2026-01-01T00:00:00Z',frame:'N_INERTIAL',sourceId:'invalid'}), false);
  assert.equal(h.api.setRunTelemetry([], {runId:'unsealed',epochUtc:'2026-01-01T00:00:00Z',verified:false}), false);
  assert.match(h.status.textContent, /演示/);
});

test('verified finite Run telemetry replaces demo mode and preserves sampling provenance', async () => {
  const h = harness(); await h.api.enter(); await h.ready();
  const rows = [
    {time_s:0,'orbit.position_m_x':7000000,'orbit.position_m_y':0,'orbit.position_m_z':0},
    {time_s:5,'orbit.position_m_x':6999900,'orbit.position_m_y':50000,'orbit.position_m_z':10000},
  ];
  assert.equal(h.api.setRunTelemetry(rows, {
    runId:'sealed-run-1',epochUtc:'2026-01-01T00:00:00Z',frame:'N_INERTIAL',
    verified:true,sampled:true,totalRows:5000
  }), true);
  assert.equal(h.instances[0].objectRows[0].sim_time_s, 5);
  assert.equal(h.instances[0].pathRows[0].points.length, 2);
  assert.match(h.status.textContent, /抽样概览/);
});

test('scene refuses an undeclared or non-inertial source frame', async () => {
  const h = harness(); await h.api.enter(); await h.ready();
  const accepted = h.api.updateTelemetry({
    sim_time_s: 0,
    values: {'orbit.position_m_x':7000000,'orbit.position_m_y':0,'orbit.position_m_z':0}
  }, {epochUtc:'2026-01-01T00:00:00Z',frame:'BODY',sourceId:'wrong-frame'});
  assert.equal(accepted,false);
  assert.match(h.status.textContent,/演示/);
});

test('real-time ground track breaks across dropped telemetry sequence ranges', async () => {
  const h = harness(); await h.api.enter(); await h.ready();
  const frame = sequence => ({
    sequence, sim_time_s: sequence,
    values: {'orbit.position_m_x':7000000-sequence*10,'orbit.position_m_y':sequence*1000,'orbit.position_m_z':0}
  });
  const options = {epochUtc:'2026-01-01T00:00:00Z',frame:'N_INERTIAL',sourceId:'gap-session'};
  h.api.updateTelemetry(frame(1), options);
  h.api.updateTelemetry(frame(2), options);
  h.api.updateTelemetry(frame(5), options);
  h.api.updateTelemetry(frame(6), options);
  assert.equal(h.instances[0].pathRows.length, 2);
  assert.equal(h.instances[0].pathRows[0].points.length, 2);
  assert.equal(h.instances[0].pathRows[1].points.length, 2);
});
