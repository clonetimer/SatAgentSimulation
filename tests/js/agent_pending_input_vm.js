/* Real app parse/project/run functions; DOM and HTTP boundaries only are fakes.
 * This supplies local behavioral evidence, not browser/model/physics acceptance. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../src/sat_sim/web/app.js'), 'utf8');
const fixture = JSON.parse(fs.readFileSync(0, 'utf8'));
const scenario = process.argv[2];
const copy = value => JSON.parse(JSON.stringify(value));
function section(start, end) {
  const first = source.indexOf(start), last = source.indexOf(end, first);
  assert.ok(first >= 0 && last > first, `Missing function boundary: ${start}`);
  return source.slice(first, last);
}
const code = [
  'const canonicalTexts = new WeakMap();',
  section('  function equivalentJSONValues(', '  function requestErrorMessage('),
  section('  function clone(', '  function codeDiffSummary('),
  section('  async function projectTaskSpecToForm(', '  function formatNumber('),
  section('  function syncTaskSpecEditor(', '  function renderAgentConversation('),
  section('  let agentGenerationPromise = null;', '  function taskSpecFromEditor('),
  section('  async function runSimulation(', '  async function loadRuns('),
].join('\n');
const elements = new Map();
function el(id) {
  if (!elements.has(id)) elements.set(id, {
    value:'', textContent:'', checked:false, disabled:false,
    setAttribute(){}, removeAttribute(){}, setSelectionRange(){}, classList:{add(){},remove(){}},
  });
  return elements.get(id);
}
let activeTab = 'agent';
const state = {
  singleRunSubmitting:false, agentGenerating:false,
  selectedCapabilityId:fixture.accepted.model.capability_id,
  schema:{default_form:copy(fixture.default_form)},
  formData:copy(fixture.accepted_form), taskSpec:copy(fixture.accepted),
  planning:{accepted:'planning'}, hasTaskSpecContext:true,
  agentHistory:[], activeRunId:null, activeTaskCenterId:null,
};
el('agentRefineCurrent').checked = true;
el('agentBackend').value = 'protocol-only';
el('agentRoutingMode').value = 'auto';
el('agentInput').value = '将仿真时长改为120秒';
el('taskSpecEditor').value = fixture.accepted_text;
const naturalRequests=[], runRequests=[], formRequests=[], notices=[];
let duringGeneration = () => {}, duringProjection = () => {}, duringPreflight = () => {};
let outcome = 'success';
let validForm = true;
const sandbox = {
  state, el, document:{querySelector:()=>({dataset:{tab:activeTab}})},
  deepEqual:(a,b)=>JSON.stringify(a)===JSON.stringify(b),
  formDiffPaths:()=>new Set(['simulation.duration_s']),
  validateClientForm:()=>validForm,
  renderForm(){}, renderEffectSelect(){}, renderEvents(){}, renderOutputs(){},
  renderAgentChangePanel(){}, renderAgentConversation(){}, clearFieldErrors(){},
  switchTab:tab=>{activeTab=tab;},
  showNotice:(text,type)=>notices.push({text,type}),
  showValidationIssues:(errors,message)=>notices.push({text:message,type:'error'}),
  formatValidationIssue:item=>item.message || item.code,
  updateSingleRunGuide(){}, clearNotice(){}, showBusy(){}, hideBusy(){},
  markSelectedRunInList(){}, renderExecutionProgress(){}, loadRuns:async()=>{}, pollRunExecution:async()=>{},
  api: async (url, options) => {
    const body=JSON.parse(options.body);
    if (url==='/tasks/parse' && body.input_kind==='natural_language') {
      naturalRequests.push(body);
      await duringGeneration();
      if (outcome==='transport_error') throw new Error('Protocol transport failed');
      const result = outcome==='reject' ? {task_spec:copy(fixture.rejected), planning:{rejected:'planning'},
        validation:{errors:[{message:'Unsupported instruction'}]}, guards:{issues:[]}}
        : {task_spec:copy(fixture.updated),task_spec_json:fixture.updated_text,
          planning:{updated:'planning'},route:{provider_id:'protocol-only'}};
      sandbox.rememberCanonicalTexts({result});
      return {ok:outcome!=='reject',request_id:'b'.repeat(32),result};
    }
    if (url==='/tasks/parse' && body.input_kind==='form') {
      formRequests.push(body);
      await duringPreflight();
      const result={task_spec:copy(fixture.edited),task_spec_json:fixture.edited_text,planning:{edited:'planning'}};
      sandbox.rememberCanonicalTexts({result});
      return {ok:true,request_id:'c'.repeat(32),result};
    }
    if (url.endsWith('/project')) {
      await duringProjection();
      if (outcome==='projection_error') throw new Error('Projection unavailable');
      return {projection:{projection_is_lossless:true, form_data:copy(fixture.updated_form)}};
    }
    if (url==='/runs') {
      runRequests.push(body);
      return {prepared_run:{run_id:'protocol-run'},execution_state:{state:'QUEUED'}};
    }
    throw new Error(`Unexpected HTTP boundary ${url}`);
  },
};
vm.createContext(sandbox);
vm.runInContext(code+'\nrememberCanonicalTexts({task_spec:state.taskSpec,task_spec_json:'+JSON.stringify(fixture.accepted_text)+'});\nsyncTaskSpecEditor();',sandbox);
const evaluate = expression => vm.runInContext(expression,sandbox);
const snapshot = () => JSON.stringify({task:state.taskSpec,form:state.formData,planning:state.planning,editor:el('taskSpecEditor').value});
(async()=>{
  state.taskSource={requestId:'a'.repeat(32),taskSpecText:fixture.accepted_text};
  const before=snapshot();
  if (scenario==='latest_pending') {
    await evaluate('runSimulation()');
    assert.equal(naturalRequests.length,1,'Run must apply pending text even with an accepted TaskSpec');
    assert.equal(naturalRequests[0].base_task_spec.simulation.duration_s,60);
    assert.equal(runRequests.length,1);
    assert.equal(runRequests[0].task_spec.simulation.duration_s,120,'Submit latest accepted instruction');
    assert.equal(runRequests[0].task_spec_json,fixture.updated_text,'Preserve canonical TaskSpec bytes');
    assert.equal(el('agentInput').value,'');
    assert.equal(runRequests[0].source_request_id,'b'.repeat(32),'Applied response receipt must accompany its task');
  } else if (['reject','transport_error','projection_error','stale_generation','stale_projection'].includes(scenario)) {
    outcome=scenario;
    if(scenario==='stale_generation') duringGeneration=()=>{el('agentInput').value='Newer pending instruction';};
    if(scenario==='stale_projection') duringProjection=()=>{el('agentInput').value='Newer pending instruction';};
    try { await evaluate('runSimulation()'); }
    catch(error) { assert.ok(['Protocol transport failed','Projection unavailable'].includes(error.message)); }
    assert.equal(runRequests.length,0,'Rejected, failed, or stale answer must not start a run');
    assert.equal(naturalRequests.length,1,'Pending instruction must be checked before any run');
    assert.equal(snapshot(),before,'Accepted task/form/planning/editor must survive unsuccessful application');
    assert.ok(el('agentInput').value.trim(),'Unapplied instruction must remain pending');
    assert.match(el('agentGenerationStatus').textContent,/未应用|待应用|待处理/,'Status must identify pending input');
    assert.equal(state.singleRunSubmitting,false);
    assert.equal(state.agentGenerating,false);
    assert.equal(state.taskSource.requestId,'a'.repeat(32),'Unapplied responses cannot capture a receipt');
  } else if (scenario==='reject_parse') {
    outcome='reject';
    assert.equal(await evaluate('parseNaturalLanguage()'),false);
    assert.equal(snapshot(),before,'Rejected answer must not replace accepted TaskSpec, planning, form, or JSON');
    assert.equal(el('agentInput').value,'将仿真时长改为120秒');
  } else if (scenario==='blank_rerun') {
    el('agentInput').value=' \n ';
    await evaluate('runSimulation()');
    assert.equal(naturalRequests.length,0);
    assert.equal(runRequests[0].task_spec.simulation.duration_s,60);
    assert.equal(runRequests[0].source_request_id,'a'.repeat(32));
  } else if (scenario==='receipt_manual_change') {
    activeTab='form'; el('agentInput').value='';
    state.taskSpec=copy(fixture.edited);
    await evaluate('runSimulation()');
    assert.equal(runRequests[0].source_request_id,undefined,'Manual replacement must not reuse incompatible receipt');
  } else if (scenario==='receipt_json_precision_change') {
    activeTab='form'; el('agentInput').value='';
    evaluate("canonicalTexts.set(state.taskSpec, canonicalText(state.taskSpec).replace('60.0','60'))");
    await evaluate('runSimulation()');
    assert.equal(runRequests[0].source_request_id,undefined,'Distinct numeric canonical text must invalidate receipt');
  } else if (scenario==='receipt_form_preview') {
    activeTab='form'; el('agentInput').value=''; state.taskSpec=null;
    state.formData=copy(fixture.edited_form);
    await evaluate('runSimulation()');
    assert.equal(runRequests[0].source_request_id,'c'.repeat(32),'Fresh preview receipt binds changed form');
    assert.equal(runRequests[0].task_spec_json,fixture.edited_text);
  } else if (scenario==='manual_edit') {
    state.formData=copy(fixture.edited_form); state.taskSpec=null;
    await evaluate('runSimulation()');
    assert.equal(formRequests.length,1,'Revalidate edited form before refinement');
    assert.equal(naturalRequests[0].base_task_spec.simulation.duration_s,90);
    assert.equal(runRequests[0].task_spec.simulation.duration_s,120);
  } else if (scenario==='manual_invalid') {
    state.taskSpec=null; validForm=false;
    await evaluate('runSimulation()');
    assert.equal(naturalRequests.length,0);
    assert.equal(runRequests.length,0);
    assert.equal(el('agentInput').value,'将仿真时长改为120秒');
  } else if (scenario==='preflight_stale') {
    state.formData=copy(fixture.edited_form); state.taskSpec=null;
    duringPreflight=()=>{el('agentInput').value='Newer pending instruction';};
    await evaluate('runSimulation()');
    assert.equal(naturalRequests.length,0,'Input changed during form preflight must not send old text');
    assert.equal(runRequests.length,0);
    assert.equal(el('agentInput').value,'Newer pending instruction');
    assert.equal(state.taskSpec.simulation.duration_s,90,'Validated manual configuration stays accepted');
  } else if (scenario==='one_owner') {
    let finish;
    duringGeneration=()=>new Promise(resolve=>{finish=resolve;});
    const first=evaluate('parseNaturalLanguage()');
    const second=evaluate('parseNaturalLanguage()');
    assert.equal(first,second,'Explicit parsing must reuse its owner');
    await evaluate('runSimulation()');
    assert.equal(naturalRequests.length,1);
    assert.equal(runRequests.length,0);
    finish(); await first;
    assert.equal(state.taskSpec.simulation.duration_s,120);
  } else throw new Error(`Unknown scenario: ${scenario}`);
  console.log(JSON.stringify({scenario,status:'PASS',natural_requests:naturalRequests.length,run_requests:runRequests.length,
    scope:'Actual parse/project/run functions in Node VM; mocked DOM/HTTP, not browser/model/physics acceptance'}));
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
