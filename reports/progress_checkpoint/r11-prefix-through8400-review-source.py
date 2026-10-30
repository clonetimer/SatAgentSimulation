"""Verify an immutable logical prefix behind the ongoing journal append point."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import math
import runpy
import sys
import psutil

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from sat_sim.interactive.telemetry_segments import iter_entries

BASE = ROOT/'reports/local_requirements_completion'
RUN = BASE/'hardware-bounded-r11-endurance/endurance-24h'
WORKSPACE = RUN/'api/interactive_sessions/native-endurance'
TARGET = 8400.
OUTPUT = BASE/'r11-durable-prefix-through8400-physical-review.json'

def read(path):
    return json.loads(path.read_text(encoding='utf-8'))

def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            value.update(chunk)
    return value.hexdigest()

native_proc = psutil.Process(80520)
assert native_proc.create_time()==1793337824.415924 and native_proc.is_running()
progress = read(RUN/'progress.json')
assert progress['status']=='RUNNING' and progress['session']['sim_time_s']>TARGET+60.
applied_path = WORKSPACE/'session_spec.json'
applied = read(applied_path)
assert applied['task_spec_sha256']==progress['applied_task_spec_sha256']=='1f100d763a44e16dcb899d7759e9dbf8749548c01e7b24d3837d6d3041825d02'
assert applied['session_spec']['task_spec']['parameters']['values']=={
    'environment_profile':'spice_wmm','epoch_utc':'2026-01-01T00:00:00Z',
    'solar_panel_structure_conductance_w_k':4.}
module = runpy.run_path(str(ROOT/'scripts/run_native_endurance.py'))
fp = module['runtime_source_fingerprint']
assert fp(module['application_source_manifest']())==progress['application_source_sha256_before']
assert fp(module['runtime_source_manifest']())==progress['runtime_source_sha256_before']
bounds_path = BASE/'thermal-link-v9-environment-campaign/nominal-independent-review.json'
bounds = read(bounds_path)['thermal_bounds_k']
assert len(bounds)==8
old_path = BASE/'graph-containment-v8-day-forecast-thermal-independent-review.json'
old = read(old_path)
old_point = next(point for point in old['first_unsafe_snapshots'] if point['trigger']=='thermal.solar_panel_safe')
assert old_point['sim_time_s']==7294. and old['mission_status']=='FAIL'
assert old_point['values']['thermal.solar_panel_temp_k']<bounds['solar_panel'][0]
early_path = BASE/'r11-durable-first1000-independent-prefix-review.json'
early = read(early_path)
segments_path = WORKSPACE/'telemetry_segments/index.json'
closed_before = read(segments_path)['segments']
source_paths = [applied_path,bounds_path,old_path,early_path,Path(__file__),
               ROOT/'src/sat_sim/interactive/telemetry_segments.py']
source_before = {str(path):digest(path) for path in source_paths}

def replay(physical):
    counts = {'runtime.trace':0,'whole.continuous':0}
    result = {'record_count':0,'counts':counts,'thermal_extrema_k':{k:[math.inf,-math.inf] for k in bounds},
              'thermal_violation_counts':{k:0 for k in bounds},'declared_unsafe_counts':{k:0 for k in bounds},
              'nonfinite_temperature_count':0,'safe_flag_disagreement_count':0,'soc_range':[math.inf,-math.inf],
              'selected_native_snapshots':{}}
    latest_native = None
    for event in iter_entries(WORKSPACE):
        frame = event['frame'];stream = frame['stream']
        assert stream in counts and event['sequence']==result['record_count']
        assert frame['session_id']=='native-endurance' and frame['sequence']==counts[stream]
        assert math.isclose(frame['sim_time_s'],counts[stream]*.5,rel_tol=0,abs_tol=1e-9)
        counts[stream]+=1;result['record_count']+=1
        if result['record_count']==early['prefix']['records']:
            assert event['entry_hash']==early['prefix']['last_entry_hash']
        values=frame['values']
        if stream=='runtime.trace':
            latest_native=(frame['sim_time_s'],values)
            if physical:
                for node,(low,high) in bounds.items():
                    value=float(values['thermal.'+node+'_temp_k']);valid=math.isfinite(value) and low<=value<=high
                    flag=values['thermal.'+node+'_safe'];assert flag in (0,1,False,True)
                    result['nonfinite_temperature_count']+=int(not math.isfinite(value))
                    result['thermal_violation_counts'][node]+=int(not valid)
                    result['declared_unsafe_counts'][node]+=int(not bool(flag))
                    result['safe_flag_disagreement_count']+=int(bool(flag)!=valid)
                    result['thermal_extrema_k'][node]=[min(result['thermal_extrema_k'][node][0],value),max(result['thermal_extrema_k'][node][1],value)]
                for stamp in (7294.,TARGET):
                    if math.isclose(frame['sim_time_s'],stamp,rel_tol=0,abs_tol=1e-9):
                        result['selected_native_snapshots'][str(stamp)]={'sim_time_s':frame['sim_time_s'],
                            'thermal_k':{node:values['thermal.'+node+'_temp_k'] for node in bounds},
                            'eps_battery_soc':values['eps.battery_soc']}
        else:
            assert latest_native is not None and frame['sim_time_s']==latest_native[0]
            assert all(key in latest_native[1] and latest_native[1][key]==value for key,value in values.items())
        soc=float(values['eps.battery_soc']);assert math.isfinite(soc) and 0<=soc<=1
        result['soc_range']=[min(result['soc_range'][0],soc),max(result['soc_range'][1],soc)]
        if stream=='whole.continuous' and frame['sim_time_s']>=TARGET:
            assert math.isclose(frame['sim_time_s'],TARGET,rel_tol=0,abs_tol=1e-9)
            result['last_entry_hash']=event['entry_hash'];result['last_sim_time_s']=frame['sim_time_s']
            break
    else:
        raise AssertionError('DURABLE_TARGET_PREFIX_NOT_AVAILABLE')
    assert counts=={'runtime.trace':16801,'whole.continuous':16801}
    return result

first=replay(True)
second=replay(False)
assert first['record_count']==second['record_count'] and first['last_entry_hash']==second['last_entry_hash']
assert read(segments_path)['segments'][:len(closed_before)]==closed_before
assert {str(path):digest(path) for path in source_paths}==source_before
assert native_proc.is_running() and native_proc.create_time()==1793337824.415924
assert fp(module['application_source_manifest']())==progress['application_source_sha256_before']
assert fp(module['runtime_source_manifest']())==progress['runtime_source_sha256_before']
physical_pass=not any(first['thermal_violation_counts'].values()) and not any(first['declared_unsafe_counts'].values()) and first['nonfinite_temperature_count']==0 and first['safe_flag_disagreement_count']==0
report={'status':'DURABLE_PREFIX_PHYSICAL_BOUNDS_PASS_ONLY_NOT24H' if physical_pass else 'DURABLE_PREFIX_PHYSICAL_FAILURE_OBSERVED',
    'observed_at_utc':datetime.now(timezone.utc).isoformat(),'native_pid':native_proc.pid,'native_created':native_proc.create_time(),
    'application':progress['application_source_sha256_before'],'core':progress['runtime_source_sha256_before'],
    'source_still_frozen':True,'explicit_current_design_assumption_w_k':4.,'original2w_record_preserved':True,
    'original2w_comparison_point':{'independent_report':str(old_path),'mission_status':old['mission_status'],
        'first_unsafe_time_s':old_point['sim_time_s'],'solar_panel_temp_k':old_point['values']['thermal.solar_panel_temp_k']},
    'current_prefix':first,'declared_thermal_bounds_k':bounds,'closed_segments_at_start':closed_before,
    'verified_early_prefix_entry_hash':early['prefix']['last_entry_hash'],'source_artifact_sha256':source_before,
    'method':'Two bounded logical replays through projected frame8400s, well behind the live append point. Existing iterator verifies global hash/sequence and completed closed-file checksums. End entry hash/count equality binds the same prefix across both reads; no live EOF consumed.',
    'scope':'Only the current distinct4W/K run through8400 simulation seconds. Not a whole mission, future margin, hardware calibration, uniform error bound, full-run timing percentile or24h uptime verdict. Does not rewrite or relabel the original2W/K failure.',
    'timing_at_initial_progress_snapshot':{key:progress['session'][key] for key in ['last_drift_s','max_abs_drift_s','timing_status']},
    'native_restarted':False,'originals_modified':False,'goal_complete':False}
with OUTPUT.open('x',encoding='utf-8') as stream:
    json.dump(report,stream,indent=2);stream.write('\n')
print(json.dumps({'status':report['status'],'records':first['record_count'],'last_sim_s':first['last_sim_time_s'],
    'old2w_solar_k_at7294':report['original2w_comparison_point']['solar_panel_temp_k'],
    'current4w_solar_k_at7294':first['selected_native_snapshots']['7294.0']['thermal_k']['solar_panel'],
    'current_solar_min_k':first['thermal_extrema_k']['solar_panel'][0],'soc_range':first['soc_range']}))
