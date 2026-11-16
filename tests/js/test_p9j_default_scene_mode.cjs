'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

// Execute the actual DOM mode controller, stopping before WebGL allocation.
const source = fs.readFileSync(path.join(__dirname, '../../src/sat_sim/web/birds-cad-viewer.js'), 'utf8');
const end = source.indexOf('const canvas=root.querySelector');
assert.ok(end > 0, 'CAD renderer boundary must be present');
const controller = source.slice(0, end).replace(/^import .*;\r?\n/gm, '');
function harness(defaultMode = 'simulation') {
  function node() {
    const classes = new Set();
    return {hidden: false, textContent: '', attributes: {}, handlers: {},
      classList: {toggle(key, value) { if (value) classes.add(key); else classes.delete(key); }, contains(key) { return classes.has(key); }},
      getAttribute(key) { return this.attributes[key] ?? null; },
      setAttribute(key, value) { this.attributes[key] = String(value); },
      addEventListener(key, fn) { this.handlers[key] = fn; },
    };
  }
  const elements = Object.fromEntries(['birdsCadViewer', 'satelliteOrbitMode', 'simulationGeometryMode', 'satelliteSceneTitle', 'satelliteSceneDescription'].map(id => [id, node()]));
  elements.satelliteOrbitMode.hidden = defaultMode !== 'orbit';
  elements.simulationGeometryMode.hidden = defaultMode !== 'simulation';
  elements.birdsCadViewer.hidden = defaultMode !== 'cad';
  const layout = node(), panel = node(), calls = [];
  const tabs = ['orbit', 'simulation', 'cad'].map(mode => Object.assign(node(), {dataset: {sceneMode: mode}}));
  tabs.forEach(tab => tab.setAttribute('aria-pressed', tab.dataset.sceneMode === defaultMode));
  const document = {
    getElementById: id => elements[id] || null,
    querySelector: selector => selector === '.p9d-scene-layout' ? layout : panel,
    querySelectorAll: () => tabs,
  };
  const context = vm.createContext({document, window: {
    BirdsCadViewer: {setViewerVisible(value) { calls.push(['cad', value]); }},
    SatScene: {
      setOrbitSceneVisible(value) { calls.push(['orbit', value]); },
      setSimulationGeometryVisible(value) { calls.push(['simulation', value]); },
    },
  }});
  vm.runInContext(controller, context);
  return {elements, layout, panel, calls, tabs, click(mode) { tabs.find(tab => tab.dataset.sceneMode === mode).handlers.click(); }};
}
test('physical default initializes layout and title without starting hidden renderers', () => {
  const h = harness();
  assert.equal(h.elements.satelliteOrbitMode.hidden, true);
  assert.equal(h.elements.simulationGeometryMode.hidden, false);
  assert.equal(h.elements.birdsCadViewer.hidden, true);
  assert.equal(h.layout.classList.contains('is-simulation-mode'), true);
  assert.equal(h.elements.satelliteSceneTitle.textContent, 'ADCS 仿真构型');
  assert.deepEqual(h.calls, []);
});
test('explicit orbit navigation resumes only the orbit renderer', () => {
  const h = harness(); h.click('orbit');
  assert.equal(h.elements.satelliteOrbitMode.hidden, false);
  assert.equal(h.elements.simulationGeometryMode.hidden, true);
  assert.equal(h.layout.classList.contains('is-simulation-mode'), false);
  assert.deepEqual(h.calls, [['cad', false], ['orbit', true], ['simulation', false]]);
});
test('CAD navigation remains explicit and physical return restores configuration panel', () => {
  const h = harness(); h.click('cad');
  assert.equal(h.panel.hidden, true);
  assert.equal(h.elements.birdsCadViewer.hidden, false);
  assert.equal(h.layout.classList.contains('is-cad-mode'), true);
  h.click('simulation');
  assert.equal(h.panel.hidden, false);
  assert.equal(h.elements.birdsCadViewer.hidden, true);
  assert.equal(h.layout.classList.contains('is-cad-mode'), false);
  assert.equal(h.tabs.find(tab => tab.dataset.sceneMode === 'simulation').getAttribute('aria-pressed'), 'true');
});

// Keep partial-channel regressions in the same CI entrypoint.
require('./test_p9j_sensor_telemetry.cjs');

// Strict independent-stream time association contract.
require('./test_p9k_telemetry_time_join.cjs');
