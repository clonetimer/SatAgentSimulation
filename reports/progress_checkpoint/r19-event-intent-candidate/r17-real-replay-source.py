"""Frozen new natural-language create/refine requests; actual model and scripts."""
from datetime import datetime, timezone
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import runpy
import subprocess
import sys
import urllib.request

SOURCE = Path(__file__).resolve().parents[2]/'reports/local_requirements_completion/event-intent-r17-candidate'
os.environ['SAT_SIM_ASSET_ROOT']=str(Path(__file__).resolve().parents[2]/'third_party')
sys.path.insert(0,str(SOURCE/'src'))

import psutil
from sat_sim.unified_agent import UnifiedAgentRequest, run_unified_agent
from sat_sim.task_models import canonicalize_task_spec, to_runtime_task_spec

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT/'reports/local_requirements_completion'
OUT = BASE/'r17-normal-form-variations'
REQUESTS = [
    {'id':'create17', 'loss':.17, 'text':
     '使用 whole_spacecraft.unified_native.v1 创建通用近地小卫星原生整星仿真脚本，Basilisk后端。'
     '总时长12秒，步长0.2秒，记录周期0.2秒。environment_profile=spice_wmm，'
     'epoch_utc=2026-01-01T00:00:00Z，battery_capacity_wh=140，initial_soc=0.65，'
     'spacecraft_mass_kg=100，solar_panel_structure_conductance_w_k=3。'
     '在第4秒使电池容量损失17%，只添加这一个故障，其余无故障。'
     '输出 eps.effective_battery_capacity_wh、eps.battery_capacity_ratio、eps.battery_soc。'},
    {'id':'refine42', 'loss':.42, 'text':
     '把电池容量损失从17%改为42%。保留原有第4秒故障时刻、140 Wh标称容量、12秒时长、'
     '0.2秒步长和记录周期、3 W/K导热系数，以及其他参数和输出；不要添加其他事件。'},
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    OUT.mkdir(exist_ok=False)
    frozen=OUT/'requests.frozen.json'
    frozen.write_text(json.dumps(REQUESTS,ensure_ascii=False,indent=2),encoding='utf-8')
    assert sha(frozen)==sha(BASE/'r11-natural-loss-variations-v2/requests.frozen.json'), 'FROZEN_REQUEST_CHANGED'
    m=runpy.run_path(str(ROOT/'scripts/run_native_endurance.py'))
    fingerprint=m['runtime_source_fingerprint']
    root_app_before=fingerprint(m['application_source_manifest']())
    app_before=fingerprint(m['application_source_manifest'](SOURCE))
    proc=psutil.Process()
    if os.name=='nt':
        proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    permitted=proc.cpu_affinity()
    selected=[number for number in permitted if number>=12]
    if selected:
        proc.cpu_affinity(selected)
    service=json.loads((BASE/'r11-current-model-service-owned-observation.json').read_text(encoding='utf-8'))['active_v5_service']
    model_proc=psutil.Process(service['pid'])
    assert abs(model_proc.create_time()-service['process_creation'])<.01 and model_proc.is_running()
    with urllib.request.urlopen('http://127.0.0.1:8031/health',timeout=10) as response:
        health=json.load(response)
    assert health['model']=='Qwen2.5-Coder-1.5B-SatAgent-v5-guided-graph-v8'
    state={'status':'RUNNING','scope':'Two new natural-language engineering cases, not arbitrary-prompt accuracy. No event JSON supplied; no label or parameter substitution; wrong cases are not executed.',
           'started_at_utc':datetime.now(timezone.utc).isoformat(),'application':app_before,'source_root':str(SOURCE),'root_application_before':root_app_before,
           'process':{'pid':proc.pid,'creation':proc.create_time(),'affinity':proc.cpu_affinity()},
           'source_sha256':sha(Path(__file__)),'requests_sha256':sha(frozen),'model_health':health,
           'measurement_context':'Isolated scoped-negation, single-fault decoding, event-preservation, unit-contract and refinement-prompt candidate with the same live model service and original native80520. Output-token budget2048 (prior runs4096), same frozen user texts and unchanged thresholds; not a same-condition accuracy comparison. No exclusive performance qualification.',
           'cases':[]}
    state_path=OUT/'state.json'
    def save():
        state_path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    save()
    previous=None
    try:
        for request in REQUESTS:
            case={'id':request['id'],'status':'RUNNING','started_at_utc':datetime.now(timezone.utc).isoformat(),'checks':{}}
            state['cases'].append(case)
            save()
            def check(name,value):
                case['checks'][name]=bool(value)
                save()
                if not value:
                    raise AssertionError(name)
            directory=OUT/request['id']
            result=run_unified_agent(UnifiedAgentRequest(input_kind='natural_language',request_text=request['text'],
                base_task_spec=previous,output_dir=directory,backend='auto',provider_id='local-modelscope',
                provider_config_path=BASE/'r17-model-provider-config/providers.json',routing_mode='local',
                compile_if_valid=True,model_timeout_s=300,model_max_output_tokens=2048,model_temperature=0,
                model_structured_output='json_schema',max_repair_attempts=1,experience_reuse_enabled=False))
            identity=result.facade_result.to_dict().get('backend_identity',{}) if result.facade_result else {}
            case['backend_identity']=identity
            case['validation']=result.validation.to_dict()
            check('actual_model_verified',identity.get('actual_model_execution_verified') is True and identity.get('actual_model_used') is True)
            check('valid_compiled',result.validation.ok and result.guards.ok and result.compiled is not None)
            check('no_template_fallback',result.task_spec.get('metadata',{}).get('agent_safety',{}).get('model_fallback_used') is not True)
            invocation=identity.get('invocation_evidence',{})
            raw=None
            for name in ['model_call.json','model_call_retry.json']:
                path=directory/name
                if not path.is_file():
                    continue
                call=json.loads(path.read_text(encoding='utf-8'))
                evidence=call.get('invocation_evidence',{})
                if evidence.get('request_id')==invocation.get('request_id') and evidence.get('verified') is True:
                    check('raw_response_hash',hashlib.sha256(call['raw_text'].encode()).hexdigest()==invocation.get('response_sha256'))
                    raw=json.loads(call['raw_text'])
                    raw=raw.get('task_spec',raw)
                    case['raw_call']={'file':str(path),'sha256':sha(path)}
            check('raw_response_bound',raw is not None)
            raw=canonicalize_task_spec(raw)
            canonical=canonicalize_task_spec(result.task_spec)
            raw_faults=raw['events']['faults']
            faults=canonical['events']['faults']
            check('raw_loss_fraction',len(raw_faults)==1 and raw_faults[0]['effect']=='eps_battery_capacity_loss'
                  and math.isclose(raw_faults[0]['magnitude'],request['loss'],rel_tol=0,abs_tol=1e-12)
                  and raw_faults[0]['start_s']==4)
            check('raw_fraction_not_substituted',len(faults)==1 and faults[0]['magnitude']==raw_faults[0]['magnitude']
                  and faults[0]['start_s']==raw_faults[0]['start_s'])
            check('only_requested_fault',not canonical['events']['degradations'] and not canonical['events']['constraints'])
            runtime=to_runtime_task_spec(result.task_spec)
            check('requested_parameters',all(runtime['parameters'].get(key)==value for key,value in
                  {'battery_capacity_wh':140.,'initial_soc':.65,'spacecraft_mass_kg':100.,
                   'solar_panel_structure_conductance_w_k':3.,'environment_profile':'spice_wmm',
                   'epoch_utc':'2026-01-01T00:00:00Z'}.items()))
            check('requested_time',runtime['simulation']['duration_s']==12 and runtime['simulation']['sample_s']==.2
                  and runtime['simulation']['solver']['step_s']==.2)
            script=Path(result.files['generated_script']).resolve()
            check('owned_script',script.is_relative_to(directory.resolve()))
            with (directory/'execute.stdout.log').open('w',encoding='utf-8') as stdout,(directory/'execute.stderr.log').open('w',encoding='utf-8') as stderr:
                completed=subprocess.run([sys.executable,str(script)],cwd=directory,stdout=stdout,stderr=stderr,timeout=180,check=False,env={**os.environ,'PYTHONPATH':str(SOURCE/'src')+os.pathsep+str(SOURCE),'SAT_SIM_ASSET_ROOT':str(ROOT/'third_party')})
            check('script_executed',completed.returncode==0)
            payload=json.loads((directory/'execute.stdout.log').read_text(encoding='utf-8'))
            check('native_validation_pass',payload.get('ok') is True and payload.get('validation_outcome',{}).get('result')=='PASS')
            dataset=Path(payload['dataset']['output_root'])
            if not dataset.is_absolute():
                dataset=directory/dataset
            dataset=dataset.resolve()
            trace=(dataset/payload['dataset']['files']['trace']).resolve()
            check('owned_trace',dataset.is_relative_to(directory.resolve()) and trace.is_relative_to(dataset))
            with trace.open(encoding='utf-8',newline='') as stream:
                rows=list(csv.DictReader(stream))
            check('sample_grid',len(rows)==61 and all(math.isclose(float(row['time_s']),index*.2,rel_tol=0,abs_tol=1e-8) for index,row in enumerate(rows)))
            before=[row for row in rows if float(row['time_s'])<4-1e-9]
            after=[row for row in rows if float(row['time_s'])>=4.2-1e-9]
            check('before_fault_capacity',bool(before) and all(math.isclose(float(row['eps.effective_battery_capacity_wh']),140,rel_tol=0,abs_tol=1e-8) for row in before))
            check('after_fault_capacity',bool(after) and all(math.isclose(float(row['eps.effective_battery_capacity_wh']),140*(1-request['loss']),rel_tol=0,abs_tol=1e-8) for row in after))
            check('after_fault_ratio',all(math.isclose(float(row['eps.battery_capacity_ratio']),1-request['loss'],rel_tol=0,abs_tol=1e-9) for row in after))
            case.update(status='PASS',finished_at_utc=datetime.now(timezone.utc).isoformat(),
                        trace={'path':str(trace),'sha256':sha(trace),'rows':len(rows)},script_sha256=sha(script))
            previous=result.task_spec
            save()
        state['status']='PASS_REQUIRES_INDEPENDENT_REVIEW'
    except BaseException as exc:
        state.update(status='FAILED_NO_SUBSTITUTION',error={'type':type(exc).__name__,'message':str(exc)})
        if state['cases']:
            state['cases'][-1]['status']='FAILED'
    finally:
        state['application_after']=fingerprint(m['application_source_manifest'](SOURCE))
        state['root_application_after']=fingerprint(m['application_source_manifest']())
        state['root_application_unchanged']=state['root_application_after']==root_app_before
        state['source_unchanged']=state['application_after']==app_before
        state['requests_unchanged']=sha(frozen)==state['requests_sha256']
        state['finished_at_utc']=datetime.now(timezone.utc).isoformat()
        save()
    print(json.dumps({'status':state['status'],'cases':[(case['id'],case['status']) for case in state['cases']],'error':state.get('error')}))
    return 0 if state['status']=='PASS_REQUIRES_INDEPENDENT_REVIEW' else 1


if __name__=='__main__':
    raise SystemExit(main())
