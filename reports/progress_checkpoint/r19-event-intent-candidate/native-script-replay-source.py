"""Replay the unchanged real R18 model-generated script with one parser fix."""
from pathlib import Path
import csv
import datetime
import hashlib
import json
import math
import os
import runpy
import shutil
import subprocess
import sys
import psutil

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT/'reports/local_requirements_completion'
SOURCE = BASE/'event-intent-r19-candidate'
ORIGIN = BASE/'r18-planned-target-variations/create17'
OUT = BASE/'r19-original-script-native-replay'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    OUT.mkdir(exist_ok=False)
    process = psutil.Process()
    process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    process.cpu_affinity([x for x in process.cpu_affinity() if x >= 12])
    module = runpy.run_path(str(ROOT/'scripts/run_native_endurance.py'))
    fp = module['runtime_source_fingerprint']
    before = module['application_source_manifest'](BASE/'event-intent-r18-candidate')
    after = module['application_source_manifest'](SOURCE)
    old = {r['path']:r['sha256'] for r in before['files']}
    changed = [r['path'] for r in after['files'] if old.get(r['path']) != r['sha256']]
    assert changed == ['src/sat_sim/bsk_engine/event_manager.py'], changed
    script = OUT/'generated_simulation.py'
    shutil.copyfile(ORIGIN/'generated_simulation.py',script)
    assert sha(script) == sha(ORIGIN/'generated_simulation.py')
    state = {'status':'RUNNING', 'scope':'Exact real-model-generated R18 script, replayed under a one-file runtime parser fix. No new inference or model-answer substitution.',
             'started_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
             'candidate_application':fp(after),'candidate_core':fp(module['runtime_source_manifest'](SOURCE)),
             'changed_from_r18':changed,'script_sha256':sha(script),
             'origin_model_call_sha256':sha(ORIGIN/'model_call.json'),'checks':{}}
    def save():
        (OUT/'state.json').write_text(json.dumps(state,indent=2),encoding='utf-8')
    def check(name, value):
        state['checks'][name]=bool(value)
        save()
        assert value,name
    save()
    try:
        with (OUT/'stdout.json').open('w',encoding='utf-8') as stdout,(OUT/'stderr.log').open('w',encoding='utf-8') as stderr:
            result = subprocess.run([sys.executable,str(script)],cwd=OUT,stdout=stdout,stderr=stderr,timeout=180,
                env={**os.environ,'PYTHONPATH':str(SOURCE/'src')+os.pathsep+str(SOURCE),'SAT_SIM_ASSET_ROOT':str(ROOT/'third_party')},check=False)
        check('script_exit_zero',result.returncode==0)
        payload=json.loads((OUT/'stdout.json').read_text(encoding='utf-8'))
        check('native_validation_pass',payload['ok'] is True and payload['validation_outcome']['result']=='PASS')
        dataset=Path(payload['dataset']['output_root'])
        dataset=(dataset if dataset.is_absolute() else OUT/dataset).resolve()
        trace=dataset/payload['dataset']['files']['trace']
        check('owned_dataset',dataset.is_relative_to(OUT))
        rows=list(csv.DictReader(trace.open(encoding='utf-8',newline='')))
        check('native_sample_grid',len(rows)==61 and all(math.isclose(float(r['time_s']),i*.2,abs_tol=1e-8) for i,r in enumerate(rows)))
        pre=[r for r in rows if float(r['time_s'])<4.]
        post=[r for r in rows if float(r['time_s'])>=4.2-1e-9]
        check('before_capacity140',pre and all(math.isclose(float(r['eps.effective_battery_capacity_wh']),140.,abs_tol=1e-8) for r in pre))
        check('after_capacity116p2',post and all(math.isclose(float(r['eps.effective_battery_capacity_wh']),116.2,abs_tol=1e-8) for r in post))
        check('after_ratio_p83',all(math.isclose(float(r['eps.battery_capacity_ratio']),.83,abs_tol=1e-12) for r in post))
        check('soc_bounds',all(0<=float(r['eps.battery_soc'])<=1 for r in rows))
        state['trace']={'path':str(trace),'sha256':sha(trace),'rows':len(rows)}
        check('unchanged_actual_script',sha(script)==sha(ORIGIN/'generated_simulation.py'))
        check('production_app_unchanged',fp(module['application_source_manifest']())=='4cce6d46062a27bc310cc168c1d9587a458c56b4692bd60cf2c60548c2698f82')
        check('candidate_source_unchanged',fp(module['application_source_manifest'](SOURCE))==state['candidate_application'])
        state['status']='NATIVE_REPLAY_PASS_REQUIRES_INDEPENDENT_REVIEW'
    except Exception as exc:
        state.update(status='FAIL',error={'type':type(exc).__name__,'message':str(exc)})
    finally:
        state['finished_at_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
        save()
    print(json.dumps({'status':state['status'],'checks':state['checks'],'error':state.get('error')}))
    return 0 if state['status']=='NATIVE_REPLAY_PASS_REQUIRES_INDEPENDENT_REVIEW' else 1


if __name__=='__main__':
    raise SystemExit(main())
