"""Read-only timing/physics inspection of the first immutable native journal segment."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import heapq
import json
import math
import psutil

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'reports/local_requirements_completion'
RUN = BASE / 'hardware-bounded-r11-endurance/endurance-24h'
WORKSPACE = RUN / 'api/interactive_sessions/native-endurance'
OUTPUT = BASE / 'r11-first-closed-segment-timing-and-physics-review.json'

def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()

def retain_top(collection, row):
    heapq.heappush(collection, row)
    if len(collection) > 8:
        heapq.heappop(collection)

native_proc = psutil.Process(80520)
assert native_proc.create_time() == 1793337824.415924 and native_proc.is_running()
index_path = WORKSPACE / 'telemetry_segments/index.json'
entry = json.loads(index_path.read_text(encoding='utf-8'))['segments'][0]
segment_path = WORKSPACE / 'telemetry_segments' / entry['filename']
assert segment_path.stat().st_size == entry['bytes']
reference_path = BASE / 'thermal-link-v9-environment-campaign/nominal-independent-review.json'
reference_bytes = reference_path.read_bytes()
bounds = json.loads(reference_bytes)['thermal_bounds_k']
assert len(bounds) == 8
old_prefix = json.loads((BASE / 'r11-durable-first1000-independent-prefix-review.json').read_text(encoding='utf-8'))
prefix_hash, file_hash = hashlib.sha256(), hashlib.sha256()
prefix_bytes, record_count, previous_hash = 0, 0, '0' * 64
stream_counts = {'runtime.trace': 0, 'whole.continuous': 0}
top_gaps, top_pairs = [], []
previous_native, last_native = None, None
extrema = {node: [math.inf, -math.inf] for node in bounds}
soc_range = [math.inf, -math.inf]
temperature_violations, nonfinite_values, last_sim_time = 0, 0, 0.
with segment_path.open('rb') as stream:
    for line in stream:
        file_hash.update(line)
        if record_count < old_prefix['prefix']['records']:
            prefix_hash.update(line)
            prefix_bytes += len(line)
        record = json.loads(line)
        entry_hash = record.pop('entry_hash')
        canonical = json.dumps(record,ensure_ascii=False,sort_keys=True,separators=(',', ':')).encode('utf-8')
        assert record['sequence'] == record_count and record['previous_hash'] == previous_hash
        assert hashlib.sha256(canonical).hexdigest() == entry_hash
        previous_hash, record_count = entry_hash, record_count + 1
        frame = record['frame']
        stream_id = frame['stream']
        assert stream_id in stream_counts and frame['sequence'] == stream_counts[stream_id]
        assert math.isclose(frame['sim_time_s'],frame['sequence'] * .5,abs_tol=1e-9,rel_tol=0)
        stream_counts[stream_id] += 1
        stamp = datetime.fromisoformat(frame['wall_time'])
        values = frame['values']
        last_sim_time = max(last_sim_time,float(frame['sim_time_s']))
        if stream_id == 'runtime.trace':
            if previous_native is not None:
                gap = (stamp - previous_native[0]).total_seconds()
                retain_top(top_gaps,(gap,frame['sequence'],previous_native[0].isoformat(),stamp.isoformat(),previous_native[1],frame['sim_time_s']))
            previous_native = last_native = (stamp,frame['sim_time_s'],frame['sequence'],values)
            for node, (low, high) in bounds.items():
                value = float(values['thermal.' + node + '_temp_k'])
                nonfinite_values += int(not math.isfinite(value))
                temperature_violations += int(not low <= value <= high)
                extrema[node] = [min(extrema[node][0],value),max(extrema[node][1],value)]
        else:
            assert last_native is not None and frame['sequence'] == last_native[2]
            assert all(key in last_native[3] and last_native[3][key] == value for key,value in values.items())
            retain_top(top_pairs,((stamp-last_native[0]).total_seconds(),frame['sequence'],last_native[0].isoformat(),stamp.isoformat(),frame['sim_time_s']))
        soc = float(values['eps.battery_soc'])
        assert math.isfinite(soc) and 0 <= soc <= 1
        soc_range = [min(soc_range[0],soc),max(soc_range[1],soc)]
assert file_hash.hexdigest() == entry['sha256'] == digest(segment_path)
assert json.loads(index_path.read_text(encoding='utf-8'))['segments'][0] == entry
assert prefix_bytes == old_prefix['prefix']['bytes'] and prefix_hash.hexdigest() == old_prefix['prefix']['sha256']
assert reference_path.read_bytes() == reference_bytes
assert native_proc.is_running() and native_proc.create_time() == 1793337824.415924
progress = json.loads((RUN / 'progress.json').read_text(encoding='utf-8'))
budget_path = ROOT / 'configs/interactive/performance_budget.json'
budget = json.loads(budget_path.read_text(encoding='utf-8'))
assert budget['claim'] == 'soft_realtime'
report = {
    'status':'TIMING_DEGRADED_RETAINED_DELAY_STAGE_LOCALIZED_CAUSE_NOT_PROVEN',
    'observed_at_utc':datetime.now(timezone.utc).isoformat(),
    'application':progress['application_source_sha256_before'],'core':progress['runtime_source_sha256_before'],
    'native_pid':native_proc.pid,'native_created':native_proc.create_time(),
    'closed_segment':{'path':str(segment_path),'bytes':entry['bytes'],'sha256':entry['sha256'],
                      'records':record_count,'frames_by_stream':stream_counts,'sim_range_s':[0.,last_sim_time]},
    'journal_checks':{'closed_segment_hash_matches_index_and_second_read':True,'global_hash_chain_and_sequence':True,
                      'per_stream_half_second_sampling':True,'projected_values_match_native_values':True,
                      'earlier_fixed_prefix_matches_current_closed_segment':True,'same_native_identity':True},
    'temperature_violations':temperature_violations,'nonfinite_temperature_values':nonfinite_values,
    'thermal_bounds_k':bounds,'thermal_extrema_k':extrema,'soc_range':soc_range,
    'largest_runtime_publication_gaps_s':sorted(top_gaps,reverse=True),
    'largest_native_to_projection_publication_gaps_s':sorted(top_pairs,reverse=True),
    'localization':'The largest measured publication delay is after runtime.advance_to returns and runtime.trace is published, before whole.continuous publication. The source interval includes synchronous durable journal append/flush/fsync, projected-field selection and OS/Python thread scheduling. Existing timestamps do not separate these causes.',
    'pacing_at_observation':{key:progress['session'].get(key) for key in ['last_drift_s','max_abs_drift_s','timing_status','pace_observation_count']},
    'measurement_boundary':'Publication gaps use UTC timestamps and are not substitutes for monotonic SoftRealtimeClock drift observations or a full-run P95 measurement.',
    'contract':{'claim':budget['claim'],'original_30min_p95_budget_ms':budget['mvp']['single_session_drift_p95_ms_at_1x_30min'],
                'overload_policy':budget['overload_policy'],'whole24h_p95':'NOT_MEASURED_BY_THIS_REVIEW',
                'all_step_strict_realtime_qualified':False},
    'source_sha256':{path.relative_to(ROOT).as_posix():digest(path) for path in [ROOT/'src/sat_sim/interactive/manager.py',ROOT/'src/sat_sim/interactive/workspace.py',ROOT/'src/sat_sim/interactive/clock.py',budget_path,Path(__file__)]},
    'reference_bounds_sha256':hashlib.sha256(reference_bytes).hexdigest(),
    'originals_modified':False,'native_restarted':False,'cause':'UNPROVEN','goal_complete':False,
    'scope':'First closed segment only. Preserves historical timing degradation; does not qualify whole mission, future thermal margins, complete24h duration, or all-step hard realtime. No process suspension, debugger, priority/affinity change, TC or restart.'}
with OUTPUT.open('x',encoding='utf-8') as stream:
    json.dump(report,stream,indent=2);stream.write('\n')
print(json.dumps({'status':report['status'],'records':record_count,'last_sim_s':last_sim_time,
                 'temperature_violations':temperature_violations,'nonfinite_temperature_values':nonfinite_values,
                 'largest_publication_gap_s':max(top_pairs)[0],'historical_pacing_max_s':progress['session']['max_abs_drift_s']}))
