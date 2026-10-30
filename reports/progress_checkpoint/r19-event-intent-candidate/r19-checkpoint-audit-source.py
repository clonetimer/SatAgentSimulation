"""Preserve real r19 outcomes and independently check raw answers and native CSV."""
from pathlib import Path
from datetime import datetime, timezone
import csv
import hashlib
import json
import runpy
import shutil
import xml.etree.ElementTree as ET
import psutil

ROOT=Path(__file__).resolve().parents[2]
LOCAL=ROOT/'reports/local_requirements_completion'
OUT=ROOT/'reports/progress_checkpoint/r19-event-intent-candidate'
REAL=LOCAL/'r19-real-end-to-end-variations'
sha=lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text(encoding='utf-8'))
checks={}
def check(name, value):
    checks[name]=bool(value)
    assert value,name

copies={
    'r19-real-campaign-state.json':REAL/'state.json',
    'r19-create17-model-call.json':REAL/'create17/model_call.json',
    'r19-refine42-model-call.json':REAL/'refine42/model_call.json',
    'r19-create17-task-spec.json':REAL/'create17/task_spec.json',
    'r19-refine42-candidate-task-spec.json':REAL/'refine42/task_spec.json',
    'r19-create17-script.py':REAL/'create17/generated_simulation.py',
    'r19-refine42-intermediate-script.py':REAL/'refine42/generated_simulation.py',
    'r19-create17-native-trace.csv':REAL/'create17/datasets/generated_task/trace.csv',
    'r19-create17-native-summary.json':REAL/'create17/datasets/generated_task/summary.json',
    'r19-refinement-unit-failure.json':LOCAL/'r19-refinement-unit-failure.json',
    'r19-refinement-reproducer-source.py':LOCAL/'reproduce_r19_refinement_unit_failure.py',
    'r19-checkpoint-audit-source.py':Path(__file__),
}
for name,source in copies.items():
    shutil.copyfile(source,OUT/name)
    check('copy_'+name,sha(source)==sha(OUT/name))
state=read(REAL/'state.json')
check('campaign_failure_retained',state['status']=='FAILED_NO_SUBSTITUTION')
check('create_driver_checks_pass',state['cases'][0]['status']=='PASS' and all(state['cases'][0]['checks'].values()))
check('refine_failure_retained',state['cases'][1]['status']=='FAILED' and state['error']['type']=='ValidationError')
check('requests_frozen',sha(LOCAL/'r11-natural-loss-variations-v2/requests.frozen.json')=='dfe9281ddd8d36819946f811ff1f0c18517d10e67e4d2fec877d4600d37851fd')
for name,amount in [('create17',.17),('refine42',.42)]:
    call=read(REAL/name/'model_call.json')
    raw=json.loads(call['raw_text'])
    check(name+'_raw_retained',call['raw_text']==(REAL/name/'raw_model_output.txt').read_text(encoding='utf-8'))
    fault=raw['faults'][0]
    check(name+'_one_actual_correct_fault',len(raw['faults'])==1 and fault['fault_type']=='eps_battery_capacity_loss' and fault['target']=='whole_spacecraft' and fault['onset_time_s']==4 and fault['magnitude']==amount)
    simulation=raw['simulation']
    check(name+'_raw_time_correct',simulation['duration_s']==12 and simulation['sample_s']==.2 and simulation.get('step_s',simulation.get('solver',{}).get('step_s'))==.2)
rows=list(csv.DictReader((OUT/'r19-create17-native-trace.csv').open(encoding='utf-8-sig')))
check('raw_csv_grid',len(rows)==61 and all(abs(float(r['time_s'])-i*.2)<1e-9 for i,r in enumerate(rows)))
check('raw_csv_before_capacity',all(abs(float(r['eps.effective_battery_capacity_wh'])-140)<1e-8 for r in rows if float(r['time_s'])<4))
check('raw_csv_after_capacity',all(abs(float(r['eps.effective_battery_capacity_wh'])-116.2)<1e-8 and abs(float(r['eps.battery_capacity_ratio'])-.83)<1e-8 for r in rows if float(r['time_s'])>=4.2))
check('raw_csv_soc',all(0<=float(r['eps.battery_soc'])<=1 for r in rows))
check('script_bound',sha(OUT/'r19-create17-script.py')==state['cases'][0]['script_sha256'])
check('trace_bound',sha(OUT/'r19-create17-native-trace.csv')==state['cases'][0]['trace']['sha256'])
failure=read(LOCAL/'r19-refinement-unit-failure.json')
check('merge_failure_reproduced',failure['status']=='REPRODUCED_REFINEMENT_UNIT_FAILURE' and any(d['value']==3 for d in failure['time_decisions']))
check('no_refine_native_trace',not (REAL/'refine42/datasets').exists())
tree=ET.parse(OUT/'r19-regression-junit.xml')
suites=[tree.getroot()] if tree.getroot().tag=='testsuite' else list(tree.getroot().iter('testsuite'))
totals={key:sum(int(s.attrib.get(key,0)) for s in suites) for key in ['tests','failures','errors','skipped']}
check('247_related_regression',totals=={'tests':247,'failures':0,'errors':0,'skipped':0})
m=runpy.run_path(str(ROOT/'scripts/run_native_endurance.py'));fp=m['runtime_source_fingerprint']
root_app=fp(m['application_source_manifest']());root_core=fp(m['runtime_source_manifest']())
check('production_app_frozen',root_app=='4cce6d46062a27bc310cc168c1d9587a458c56b4692bd60cf2c60548c2698f82')
check('production_core_frozen',root_core=='59d0f3613c8f21e267d8c2fd7886bb3af6a4be71a32f521d3e8b4646133a9cbe')
originals=[]
for pid,created in [(82756,1793337780.3809385),(22936,1793337818.0322924),(80520,1793337824.415924),(46364,1793339200.2216158)]:
    process=psutil.Process(pid)
    exact=abs(process.create_time()-created)<.01
    check('original_identity_'+str(pid),exact and process.is_running())
    originals.append({'pid':pid,'create_time':process.create_time(),'running':process.is_running()})
progress=read(LOCAL/'hardware-bounded-r11-endurance/endurance-24h/progress.json')
observation={'observed_utc':datetime.now(timezone.utc).isoformat(),'originals':originals,'root_app':root_app,'root_core':root_core,
             'status':progress['status'],'wall_seconds':progress['wall_observed_by_runner_s'],'session':progress['session'],
             'scope':'Read-only observation of the same original run, not 24h qualification. Concurrent UI/native diagnostic load disclosed.'}
(OUT/'original-endurance-owned-observation.json').write_text(json.dumps(observation,indent=2),encoding='utf-8')
report={'status':'SCOPED_EVIDENCE_CHECKS_PASS_CAMPAIGN_STILL_FAILED','checks':checks,'related_junit':totals,
        'scope':'Independent file/CSV/raw-answer checks in a new process; no inference, script execution, answer repair or threshold changes. Does not qualify the full app, new core, broad AI accuracy or 24h timing.',
        'created_utc':datetime.now(timezone.utc).isoformat()}
(OUT/'r19-independent-artifact-review.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({'status':report['status'],'checks':len(checks),'original_wall_hours':observation['wall_seconds']/3600}),flush=True)
