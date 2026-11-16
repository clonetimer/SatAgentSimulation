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
  return {host, label, render(values, wheelPhases = null) {
    context.renderSensorTelemetry(values, '已封存有限 Run 遥测', 2, wheelPhases);
  }, row(name) {
    const row = host.children.find(row => row.children[0].textContent === name);
    return row?.children.map(cell => cell.textContent);
  }};
}
test('speed-only telemetry retains provenance and does not invent torque authority', () => {
  const h = harness(); h.render({'adcs.rw.speed_rad_s_0': 200});
  assert.match(h.label.textContent, /已封存有限 Run 遥测/);
  assert.match(h.label.textContent, /2\.000 s/);
  assert.match(h.row('反作用轮 RW1')[1], /状态 200\.00/);
  assert.match(h.row('反作用轮 RW1')[1], /限幅 —/);
  assert.equal(h.row('反作用轮 RW4'), undefined);
});
test('torque-authority-only telemetry retains provenance without inventing speed', () => {
  const h = harness(); h.render({'adcs.rw.effective_max_torque_nm_0': 0.25});
  assert.match(h.label.textContent, /已封存有限 Run 遥测/);
  assert.match(h.row('反作用轮 RW1')[1], /状态 —/);
  assert.match(h.row('反作用轮 RW1')[1], /限幅 0\.2500/);
});
test('zero wheel speed is observed data rather than missing data', () => {
  const h = harness(); h.render({'adcs.rw.speed_rad_s_0': 0});
  assert.match(h.label.textContent, /已封存有限 Run 遥测/);
  assert.match(h.row('反作用轮 RW1')[1], /状态 0\.00/);
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
  assert.match(h.row('反作用轮 RW1')[1], /限幅 —/);
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

test('native Basilisk sensor channel names render without substituting configured parameters', () => {
  const h = harness();
  h.render({
    'adcs.imu.gyro_x_rad_s':0.01,'adcs.imu.gyro_y_rad_s':0.02,'adcs.imu.gyro_z_rad_s':0.03,
    'adcs.imu.accel_x_m_s2':1,'adcs.imu.accel_y_m_s2':2,'adcs.imu.accel_z_m_s2':3,
    'adcs.magnetometer.field_x_t':1e-5,'adcs.magnetometer.field_y_t':2e-5,'adcs.magnetometer.field_z_t':3e-5,
    'adcs.star_tracker.q0':1,'adcs.star_tracker.q1':0,'adcs.star_tracker.q2':0,'adcs.star_tracker.q3':0,
    'adcs.star_tracker.valid':1,
    'adcs.sun_sensor.intensity':0.75,'adcs.sun_sensor.direction_x':0,'adcs.sun_sensor.direction_y':0,'adcs.sun_sensor.direction_z':-1,
    'adcs.sun_sensor.valid':0,
  });
  assert.match(h.row('IMU 陀螺')[1], /0\.0100, 0\.0200, 0\.0300/);
  assert.match(h.row('IMU 加速度计')[1], /1\.0000, 2\.0000, 3\.0000/);
  assert.match(h.row('磁强计')[1], /1\.0000e-5|1\.0000e-5/);
  assert.match(h.row('星敏感器')[1], /1\.0000, 0\.0000, 0\.0000, 0\.0000/);
  assert.equal(h.row('星敏感器')[2], '有效');
  assert.match(h.row('太阳敏感器')[1], /0\.75000/);
  assert.match(h.row('太阳敏感器')[2], /无效/);
});

const sceneSource = fs.readFileSync(path.join(__dirname, '../../src/sat_sim/web/satellite-scene.js'), 'utf8');
const associationStart = sceneSource.indexOf('function advanceWheelPhaseState(');
const associationEnd = sceneSource.indexOf('function pointFromTelemetry', associationStart);
assert.ok(associationStart >= 0 && associationEnd > associationStart,
  'native ADCS-to-scene measurement association must exist');
function associationHarness() {
  const point = {
    sim_time_s:2, sequence:7, session_id:'session-A', stream:'whole.orbit',
    sensorMeasurements:{'adcs.imu.gyro_x_rad_s':-1},
  };
  const ui = {};
  const state = {
    latestTelemetryPoint:point, telemetrySourceId:'session-A', telemetryEpochUtc:'2026-01-01T00:00:00Z',
    telemetryTrail:[point], wheelPhaseStates:{},
  };
  const context = vm.createContext({
    state,
    ADCS_SCENE_FIELDS:['adcs.rw.speed_rad_s_0','adcs.rw.speed_rad_s_1','adcs.rw.speed_rad_s_2'],
    setText(id,value){ui[id]=value;},
    renderTelemetryPosition(){},
  });
  vm.runInContext(sceneSource.slice(associationStart,associationEnd)
    + '\nglobalThis.updateAdcsTelemetry = updateAdcsTelemetry;', context);
  return {point,ui,update:context.updateAdcsTelemetry};
}
test('strictly associated native ADCS frame maps real Basilisk IMU, CSS, star and magnetometer outputs', () => {
  const h=associationHarness();
  const values={
    'adcs.rw.speed_rad_s_0':10,'adcs.rw.speed_rad_s_1':20,'adcs.rw.speed_rad_s_2':30,
    'adcs.sensor.gyro_measured_rad_s_x':0.01,
    'adcs.sensor.gyro_measured_rad_s_y':0.02,
    'adcs.sensor.gyro_measured_rad_s_z':0.03,
    'adcs.sensor.accel_measured_m_s2_x':1,
    'adcs.sensor.accel_measured_m_s2_y':2,
    'adcs.sensor.accel_measured_m_s2_z':3,
    'adcs.star_tracker.q0':1,'adcs.star_tracker.q1':0,
    'adcs.star_tracker.q2':0,'adcs.star_tracker.q3':0,'adcs.star_tracker.valid':1,
    'adcs.magnetometer.tesla_x':1e-5,'adcs.magnetometer.tesla_y':2e-5,'adcs.magnetometer.tesla_z':3e-5,
    'adcs.sun_sensor.cosine_output':0.75,
    'adcs.sun_sensor.direction_b_x':0,'adcs.sun_sensor.direction_b_y':0,'adcs.sun_sensor.direction_b_z':-1,
    'adcs.sun_sensor.valid':0,
  };
  const pair={
    session_id:'session-A',epoch_utc:'2026-01-01T00:00:00Z',frame:'N_INERTIAL',time_system:'UTC',sim_time_s:2,
    orbit:{session_id:'session-A',stream:'whole.orbit',sequence:7,sim_time_s:2,quality:'GOOD'},
    adcs:{session_id:'session-A',stream:'whole.adcs',sequence:7,sim_time_s:2,quality:'GOOD',values},
  };
  const accepted=h.update(pair,{
    sourceId:'session-A',epochUtc:'2026-01-01T00:00:00Z',frame:'N_INERTIAL',
    timeSystem:'UTC',orbitStreamId:'whole.orbit',adcsStreamId:'whole.adcs',
  });
  assert.equal(accepted,true);
  assert.equal(h.point.sensorMeasurements['adcs.imu.gyro_x_rad_s'],0.01);
  assert.equal(h.point.sensorMeasurements['adcs.imu.accel_z_m_s2'],3);
  assert.equal(h.point.sensorMeasurements['adcs.star_tracker.q0'],1);
  assert.equal(h.point.sensorMeasurements['adcs.star_tracker.valid'],1);
  assert.equal(h.point.sensorMeasurements['adcs.magnetometer.field_y_t'],2e-5);
  assert.equal(h.point.sensorMeasurements['adcs.sun_sensor.intensity'],0.75);
  assert.equal(h.point.sensorMeasurements['adcs.sun_sensor.valid'],0);
  assert.equal(h.point.sensorMeasurements['adcs.imu.gyro_x_rad_s'],0.01);
});


const contextStart = sceneSource.indexOf('function lifecycleStateLabel(');
const contextEnd = sceneSource.indexOf('function syncBodyAxes()', contextStart);
assert.ok(contextStart >= 0 && contextEnd > contextStart,
  'scene provenance renderer and session context updater must exist');
function provenanceHarness() {
  const values = {};
  const source = {textContent:'',dataset:{}};
  const point = {
    sim_time_s:12.5, altitude_km:550, lat:1, lng:2, sequence:8,
    session_id:'session-A',stream:'whole.orbit',quality:'GOOD',
    attitude_valid:false, attitude_truth:null,
    adcsSource:{session_id:'session-A',stream:'whole.adcs',sequence:8,
      sim_time_s:12.5,quality:'GOOD',sequence_gap_before:false},
  };
  const state = {
    telemetrySourceId:'session-A', telemetryStreamId:'whole.orbit',
    telemetrySourceLabel:'真实 Basilisk 轨道 + ADCS 遥测',
    telemetryEpochUtc:'2026-01-01T00:00:00Z',
    telemetryCapabilityId:'whole_spacecraft.unified_native.v1',
    telemetryLifecycleState:'RUNNING',
    adcsTelemetryStatus:{quality:'GOOD',simTimeS:12.5,reason:null,source:point.adcsSource},
    latestTelemetryPoint:point,
  };
  const context = vm.createContext({
    state,
    byId:id=>id==='sceneDataSource'?source:null,
    setText:(id,value)=>{values[id]=value;},
  });
  vm.runInContext(sceneSource.slice(contextStart,contextEnd)
    + '\nglobalThis.renderTelemetryInspector = renderTelemetryInspector;'
    + '\nglobalThis.setTelemetrySessionContext = setTelemetrySessionContext;',context);
  return {state,values,source,render:context.renderTelemetryInspector,set:context.setTelemetrySessionContext};
}
test('scene provenance shows source, capability, lifecycle, simulation time and separate ADCS quality',()=>{
  const h=provenanceHarness();h.render();
  assert.equal(h.values.sceneTaskContextState,'实时仿真 · 已关联');
  assert.equal(h.values.sceneTaskContextId,'session-A');
  assert.equal(h.values.sceneTaskContextCapability,'whole_spacecraft.unified_native.v1');
  assert.equal(h.values.sceneTaskContextLifecycle,'运行中');
  assert.equal(h.values.sceneTaskContextTime,'12.50 s');
  assert.equal(h.values.sceneTaskContextQuality,'GOOD');
  assert.equal(h.values.sceneTaskContextAdcsQuality,'GOOD · 12.50 s');
  assert.match(h.values.sceneAdcsFrameSource,/whole.adcs.*GOOD/);
});
test('session lifecycle updates only for the active telemetry source',()=>{
  const h=provenanceHarness();
  assert.equal(h.set({sourceId:'other-session',lifecycleState:'STOPPED'}),false);
  assert.equal(h.state.telemetryLifecycleState,'RUNNING');
  assert.equal(h.set({sourceId:'session-A',lifecycleState:'PAUSED'}),true);
  assert.equal(h.values.sceneTaskContextLifecycle,'已暂停');
});
test('missing ADCS association is shown independently instead of inheriting orbit GOOD quality',()=>{
  const h=provenanceHarness();
  h.state.latestTelemetryPoint.adcsSource=null;
  h.state.adcsTelemetryStatus={quality:'UNAVAILABLE',simTimeS:null,reason:'ADCS 遥测序列缺口 2'};
  h.render();
  assert.equal(h.values.sceneTaskContextQuality,'GOOD');
  assert.equal(h.values.sceneTaskContextAdcsQuality,'ADCS 遥测序列缺口 2');
  assert.match(h.values.sceneAdcsFrameSource,/ADCS 遥测序列缺口 2/);
});


const pendingStart = sceneSource.indexOf('function setAdcsTelemetryPending(');
const pendingEnd = sceneSource.indexOf('function invalidateAdcsTelemetry(', pendingStart);
assert.ok(pendingStart >= 0 && pendingEnd > pendingStart,
  'pending ADCS join state must be explicit');
test('an unmatched orbit or ADCS frame supersedes stale GOOD status with pending',()=>{
  const state={adcsTelemetryStatus:{quality:'GOOD',simTimeS:4,reason:null}};
  let rendered=0;
  const context=vm.createContext({state,renderTelemetryInspector(){rendered+=1;}});
  vm.runInContext(sceneSource.slice(pendingStart,pendingEnd)
    + '\nglobalThis.setAdcsTelemetryPending = setAdcsTelemetryPending;',context);
  assert.equal(context.setAdcsTelemetryPending('等待精确配对 · orbit seq 9'),true);
  assert.equal(state.adcsTelemetryStatus.quality,'PENDING');
  assert.equal(state.adcsTelemetryStatus.simTimeS,null);
  assert.match(state.adcsTelemetryStatus.reason,/orbit seq 9/);
  assert.equal(rendered,1);
});

test('sealed Run ADCS source remains visible without a live-session status object',()=>{
  const h=provenanceHarness();
  h.state.telemetrySourceId='run:sealed-A';
  h.state.telemetrySourceLabel='已封存有限 Run 遥测';
  h.state.telemetryCapabilityId='whole_spacecraft.unified_native.v1';
  h.state.telemetryLifecycleState='SUCCEEDED';
  h.state.adcsTelemetryStatus={quality:'UNAVAILABLE',simTimeS:null,reason:'封存 Run 尚未提供 ADCS 同时刻记录'};
  h.state.latestTelemetryPoint.adcsSource={
    session_id:'run:sealed-A',stream:'sealed.run.telemetry',sequence:3,
    sim_time_s:12.5,quality:'GOOD',sequence_gap_before:false,
  };
  h.render();
  assert.equal(h.values.sceneTaskContextAdcsQuality,'GOOD · 12.50 s');
  assert.match(h.values.sceneAdcsFrameSource,/sealed.run.telemetry.*GOOD/);
});
