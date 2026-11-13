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
    console: { warn() {}, error() {} }
  });
  vm.runInContext(source.replace(/^import \* as THREE from 'three';\s*/, ''), context,
    {filename: 'satellite-scene.js'});
  return { api: window.SatScene, instances, observers, host, status, frames, timers, buttons,
    async ready(index = instances.length - 1) {
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
