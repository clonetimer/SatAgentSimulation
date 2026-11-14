'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('P9-E binds sealed Run series at exact object, subsystem and whole-spacecraft scopes', () => {
  const window = {};
  vm.runInNewContext(fs.readFileSync('src/sat_sim/web/object-run-results.js', 'utf8'), {window, Map, Number, String, Boolean, Array, Object});
  const tree = {roots:[{object_id:'whole_spacecraft',kind:'whole_spacecraft',name_zh:'整星',children:[
    {object_id:'subsystem.adcs',kind:'subsystem',name_zh:'姿态控制分系统',children:[
      {object_id:'component.reaction_wheel',kind:'equipment',name_zh:'反作用轮组',children:[]}
    ]},
    {object_id:'subsystem.eps',kind:'subsystem',name_zh:'能源分系统',children:[]}
  ]}]};
  const run = {run_id:'run-1',sealed:true,integrity:{ok:true},run_record:{status:'SUCCEEDED'}};
  const manifest = {schema_version:'sat-sim.plot-manifest.v3',time_axis:{field:'time_s'},series:[
    {field:'adcs.body_rate_rad_s_x',label:'X轴角速度',unit:'rad/s',object_id:'subsystem.adcs',association_scope:'subsystem',association_source:'capability_output_hierarchy'},
    {field:'adcs.reaction_wheel.speed_rad_s_0',label:'反作用轮角速度',unit:'rad/s',object_id:'component.reaction_wheel',association_scope:'component',association_source:'workbench_quantity_dictionary.v2'},
    {field:'eps.battery_soc',label:'电池荷电状态',unit:'ratio',object_id:'subsystem.eps',association_scope:'subsystem',association_source:'capability_output_hierarchy'}
  ]};
  const rows = [
    {time_s:0, 'adcs.body_rate_rad_s_x':0.1,'adcs.reaction_wheel.speed_rad_s_0':10,'eps.battery_soc':0.8},
    {time_s:2, 'adcs.body_rate_rad_s_x':0.2,'adcs.reaction_wheel.speed_rad_s_0':12,'eps.battery_soc':0.79}
  ];
  const component = window.P9EObjectRunResults.resolve({tree,selectedId:'component.reaction_wheel',run,plotManifest:manifest,telemetryRows:rows,telemetryOverview:{sampled:true,total:100}});
  assert.equal(component.status,'available');
  assert.equal(component.series.length,1);
  assert.equal(component.series[0].field,'adcs.reaction_wheel.speed_rad_s_0');
  assert.deepEqual(JSON.parse(JSON.stringify(component.series[0].sample)),{time_s:2,value:12});
  assert.equal(component.relatedSeries.length,1);
  assert.equal(component.relatedSeries[0].object_id,'subsystem.adcs');
  assert.equal(component.sampled,true);
  assert.equal(component.totalTelemetryRows,100);

  const subsystem = window.P9EObjectRunResults.resolve({tree,selectedId:'subsystem.adcs',run,plotManifest:manifest,telemetryRows:rows});
  assert.equal(subsystem.series.length,2);
  const whole = window.P9EObjectRunResults.resolve({tree,selectedId:'whole_spacecraft',run,plotManifest:manifest,telemetryRows:rows});
  assert.equal(whole.series.length,3);
});

test('P9-E never treats subsystem-only telemetry as a single equipment output', () => {
  const window = {};
  vm.runInNewContext(fs.readFileSync('src/sat_sim/web/object-run-results.js', 'utf8'), {window, Map, Number, String, Boolean, Array, Object});
  const tree = {roots:[{object_id:'whole_spacecraft',kind:'whole_spacecraft',children:[
    {object_id:'subsystem.adcs',kind:'subsystem',children:[
      {object_id:'component.reaction_wheel',kind:'equipment',children:[]}
    ]}
  ]}]};
  const result = window.P9EObjectRunResults.resolve({
    tree,selectedId:'component.reaction_wheel',
    run:{run_id:'r',sealed:true,integrity:{ok:true},run_record:{status:'SUCCEEDED'}},
    plotManifest:{series:[{field:'adcs.rw.speed_rad_s_0',object_id:'subsystem.adcs',association_scope:'subsystem'}]},
    telemetryRows:[{time_s:1,'adcs.rw.speed_rad_s_0':10}]
  });
  assert.equal(result.status,'parent_scope_only');
  assert.equal(result.series.length,0);
  assert.equal(result.relatedSeries.length,1);
});

test('P9-E rejects unsealed, failed or integrity-invalid Runs as verified object results', () => {
  const window = {};
  vm.runInNewContext(fs.readFileSync('src/sat_sim/web/object-run-results.js', 'utf8'), {window, Map, Number, String, Boolean, Array, Object});
  const tree = {roots:[{object_id:'whole_spacecraft',kind:'whole_spacecraft',children:[]}]};
  for (const run of [
    {run_id:'a',sealed:false,integrity:{ok:true},run_record:{status:'SUCCEEDED'}},
    {run_id:'b',sealed:true,integrity:{ok:false},run_record:{status:'SUCCEEDED'}},
    {run_id:'c',sealed:true,integrity:{ok:true},run_record:{status:'FAILED'}}
  ]) {
    const result = window.P9EObjectRunResults.resolve({tree,selectedId:'whole_spacecraft',run,plotManifest:{series:[]}});
    assert.equal(result.status,'unverified');
  }
});
