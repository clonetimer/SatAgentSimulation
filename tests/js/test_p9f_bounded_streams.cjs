'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source=fs.readFileSync('src/sat_sim/web/app.js','utf8');

async function createWithTopology(enabled) {
  const a=source.indexOf('const INTERACTIVE_CAPABILITIES = {');
  const b=source.indexOf('const INTERACTIVE_TARGET_LABELS',a);
  const f=source.indexOf('async function createInteractiveSession()');
  const end=source.indexOf('function renderInteractiveSnapshot()',f);
  assert.ok(a>=0 && b>a && f>=0 && end>f);
  const values={interactiveCapability:'whole_spacecraft.unified_native.v1',interactiveDuration:'120',interactiveQuantum:'0.5',interactiveInitialRate:'1'};
  const controls=new Map(Object.entries(values).map(([id,value])=>[id,{value}]));
  controls.set('interactivePaced',{checked:true});controls.set('interactiveContinuous',{checked:false});
  const sent=[];
  const state={interactivePreparedNativeValues:{propulsion_enabled:enabled}};
  const context=vm.createContext({state,structuredClone,Date,Math,Set,Array,Number,
    el:id=>controls.get(id) || {},crypto:{randomUUID:()=> '12345678-1234-1234-1234-123456789abc'},
    api:async (path,options)=> {if(options)sent.push(JSON.parse(options.body));return {session_id:'test'};},
    apiBody:JSON.stringify,interactiveCreationSupport:()=>({supported:true}),
    renderInteractiveCreationPolicy(){},loadInteractiveSessions:async()=>{},selectInteractiveSession:async()=>{},interactiveNotice(){}});
  vm.runInContext(source.slice(a,b)+source.slice(f,end)+'; this.invokeCreate=createInteractiveSession;',context);
  await context.invokeCreate();
  assert.equal(sent.length,1);
  return sent[0];
}

test('bounded default topology does not request nonexistent propulsion measurements',async()=>{
  const request=await createWithTopology(false);
  const streams=request.task_spec.outputs.telemetry_streams;
  for(const stream of streams) {
    const unsupported=stream.fields.filter(name=>name.startsWith('propulsion.')&&name!=='propulsion.enabled');
    assert.deepEqual(unsupported,[],'P9F_DISABLED_PROPULSION_POISONS_FRAME_QUALITY');
  }
  assert.ok(streams.some(stream=>['x','y','z'].every(axis=>stream.fields.includes('orbit.position_m_'+axis))));
});

test('explicitly enabled native propulsion retains its measurement subscriptions',async()=>{
  const request=await createWithTopology(true);
  assert.equal(request.task_spec.parameters.values.propulsion_enabled,true);
  const fields=request.task_spec.outputs.telemetry_streams.flatMap(stream=>stream.fields);
  for(const name of ['propulsion.fuel_mass_kg','propulsion.total_thrust_n','propulsion.burn_active']) assert.ok(fields.includes(name));
});
