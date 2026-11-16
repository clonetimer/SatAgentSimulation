'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

// Run the actual inspector renderer. These are UI contract fixtures, not native
// Basilisk evidence; the browser/native workflows remain independent gates.
const source = fs.readFileSync(path.join(__dirname, '../../src/sat_sim/web/satellite-scene.js'), 'utf8');
const start = source.indexOf('function renderSensorTelemetry(');
const end = source.indexOf('function renderTelemetryPosition()', start);
assert.ok(start >= 0 && end > start, 'inspector renderer must be present');
const renderer = source.slice(start, end);
function harness() {
  function node() {
    return {children: [], textContent: '',
      replaceChildren() { this.children = []; },
      append(...children) { this.children.push(...children); },
    };
  }
  const host = node(), label = node();
  const context = vm.createContext({
    byId: id => id === 'simulationSensorTelemetryRows' ? host : label,
    document: {createElement: () => node()},
    updateSimulationWheelMotion() {},
  });
  vm.runInContext(renderer, context);
  return {host, label, render(values) {
    context.renderSensorTelemetry(values, '已封存有限 Run 遥测', 2);
  }, row(name) {
    const row = host.children.find(row => row.children[0].textContent === name);
    return row?.children.map(cell => cell.textContent);
  }};
}
test('speed-only telemetry retains provenance and does not invent torque authority', () => {
  const h = harness(); h.render({'adcs.rw.speed_rad_s_0': 200});
  assert.match(h.label.textContent, /已封存有限 Run 遥测/);
  assert.match(h.label.textContent, /2\.000 s/);
  assert.match(h.row('反作用轮 RW1')[1], /200\.000 rad\/s/);
  assert.match(h.row('反作用轮 RW1')[1], /力矩能力未提供/);
  assert.equal(h.row('反作用轮 RW4'), undefined);
});
test('torque-authority-only telemetry retains provenance without inventing speed', () => {
  const h = harness(); h.render({'adcs.rw.effective_max_torque_nm_0': 0.25});
  assert.match(h.label.textContent, /已封存有限 Run 遥测/);
  assert.match(h.row('反作用轮 RW1')[1], /转速未提供/);
  assert.match(h.row('反作用轮 RW1')[1], /0\.2500 N·m/);
});
test('zero wheel speed is observed data rather than missing data', () => {
  const h = harness(); h.render({'adcs.rw.speed_rad_s_0': 0});
  assert.match(h.label.textContent, /已封存有限 Run 遥测/);
  assert.match(h.row('反作用轮 RW1')[1], /0\.000 rad\/s/);
});
test('published false validity remains invalid telemetry with its source', () => {
  const h = harness(); h.render({'adcs.star_tracker.valid': false});
  assert.match(h.label.textContent, /已封存有限 Run 遥测/);
  assert.equal(h.row('星敏感器')[2], '无效');
  assert.match(h.row('星敏感器')[1], /未提供完整三轴测量/);
});
test('empty or non-finite data cannot claim published measurements', () => {
  const h = harness();
  for (const value of [null, {}, {'adcs.rw.speed_rad_s_0': NaN}, {'adcs.rw.speed_rad_s_0': Infinity}, {'adcs.rw.speed_rad_s_0': '200'}]) {
    h.render(value);
    assert.match(h.label.textContent, /没有发布/);
    assert.equal(h.row('反作用轮 RW1'), undefined);
  }
});
test('replacing a complete sample with a partial sample clears stale fields', () => {
  const h = harness();
  h.render({'adcs.rw.speed_rad_s_0': 10, 'adcs.rw.effective_max_torque_nm_0': 0.25});
  h.render({'adcs.rw.speed_rad_s_0': 20});
  assert.match(h.label.textContent, /已封存有限 Run 遥测/);
  assert.match(h.row('反作用轮 RW1')[1], /力矩能力未提供/);
  assert.doesNotMatch(h.row('反作用轮 RW1')[1], /0\.2500/);
});

test('wheel phase provenance distinguishes relative origin from unavailable phase', () => {
  const h = harness();
  h.render({'adcs.rw.speed_rad_s_0': 100}, {
    0:{valid:true,reason:'relative-rebase',phaseRad:0},
  });
  assert.equal(h.row('反作用轮 RW1')[2], '相对基准已重置');
  h.render({'adcs.rw.speed_rad_s_0': 100}, {
    0:{valid:true,reason:null,phaseRad:100},
  });
  assert.equal(h.row('反作用轮 RW1')[2], '相对累计（非绝对相位）');
  h.render({'adcs.rw.speed_rad_s_0': 100}, {
    0:{valid:false,reason:'missing-or-invalid-sample',phaseRad:null},
  });
  assert.equal(h.row('反作用轮 RW1')[2], '相位不可用');
});
