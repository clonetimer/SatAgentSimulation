"""Read-only raw evidence review; original diagnostic failures remain intact."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def percentile(values, fraction):
    ordered = sorted(values)
    offset = (len(ordered)-1)*fraction
    low = int(offset)
    high = min(low+1, len(ordered)-1)
    return ordered[low]+(ordered[high]-ordered[low])*(offset-low)


def review(name, *, wrapper_exit_bug=False):
    root = BASE/name
    diagnostic = json.loads((root/'diagnostic.json').read_text(encoding='utf-8'))
    progress = json.loads((root/'native-run/progress.json').read_text(encoding='utf-8'))
    checks = {}
    artifacts = {}
    for filename in ['timing.jsonl', 'pacing.jsonl']:
        actual = digest(root/filename)
        artifacts[filename] = actual
        checks[filename+'_hash'] = actual == diagnostic[filename+'_sha256']
    events = [json.loads(line) for line in (root/'timing.jsonl').read_text(encoding='utf-8').splitlines()]
    pacing = [json.loads(line) for line in (root/'pacing.jsonl').read_text(encoding='utf-8').splitlines()]
    checks['span_order_valid'] = all(row['end_ns'] >= row['start_ns'] for row in events)
    checks['profile_not_truncated'] = diagnostic['summary']['overflow'] is False
    checks['event_count'] = len(events) == diagnostic['summary']['events']
    checks['all_pace_observations'] = len(pacing) == progress['session']['pace_observation_count']
    checks['pace_grid'] = [row['sim_time_s'] for row in pacing] == [.5*(index+1) for index in range(len(pacing))]
    checks['monotonic_pace_wall'] = all(b['wall_time_s'] >= a['wall_time_s'] for a,b in zip(pacing,pacing[1:]))
    values = [abs(row['drift_s'])*1000 for row in pacing]
    checks['p95_recomputed'] = percentile(values,.95) == diagnostic['summary']['abs_drift_p95_ms']
    checks['maximum_recomputed'] = max(values) == diagnostic['summary']['abs_drift_max_ms']
    checks['maximum_matches_session'] = max(values) == progress['session']['max_abs_drift_s']*1000
    checks['source_and_assets_unchanged'] = all(diagnostic[key] is True for key in
        ['root_application_unchanged','candidate_application_unchanged','shared_assets_unchanged'])
    checks['native_smoke_complete'] = progress['status'] == 'SMOKE_COMPLETE_NOT_24H'
    checks['sealed_no_terminal_error'] = progress['session']['archive_status'] == 'SEALED' and progress['session']['terminal_error'] is None
    checks['no_false_24h_pass'] = progress['audit']['verdict'] == 'NOT_QUALIFIED'
    if wrapper_exit_bug:
        checks['wrapper_bug_explicit'] = diagnostic['status'] == 'FAILED' and diagnostic['error'] == {'type':'SystemExit','message':'0'}
        checks['injected_delay_recorded'] = len(diagnostic['injection']) == 1
        checks['injected_delay_measured'] = any(row['stage']=='diagnostic.injected_pre_fsync_sleep' and row['end_ns']-row['start_ns'] >= 2_000_000_000 for row in events)
        fsync = [row for row in events if row['stage']=='workspace.py:append_telemetry/real_fsync']
        checks['real_fsync_separated_from_injected_sleep'] = bool(fsync) and max(row['end_ns']-row['start_ns'] for row in fsync) < 2_000_000_000
    else:
        checks['wrapper_complete'] = diagnostic['status'] == 'DIAGNOSTIC_CAPTURED_NOT_QUALIFIED'
        checks['no_injection'] = diagnostic['injection'] == []
    records = []
    trace = root/'native-run/api/interactive_sessions/native-endurance/telemetry.jsonl'
    previous = '0'*64
    native = {}
    for sequence, line in enumerate(trace.read_text(encoding='utf-8').splitlines()):
        item = json.loads(line)
        claimed = item.pop('entry_hash')
        canonical = json.dumps(item,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
        assert item['sequence'] == sequence and item['previous_hash'] == previous
        assert hashlib.sha256(canonical).hexdigest() == claimed
        previous = claimed
        records.append(item)
        frame = item['frame']
        if frame['stream'] == 'runtime.trace':
            native[frame['sim_time_s']] = frame['values']
        else:
            assert frame['stream'] == 'whole.continuous'
            assert all(native[frame['sim_time_s']][key] == value for key,value in frame['values'].items())
    checks['telemetry_count'] = len(records) == 2*(len(pacing)+1)
    # Native recorder timestamps are integer nanoseconds converted to float;
    # e.g. 15.0 can be emitted as 15.000000000000002. Check its integer grid.
    checks['native_grid'] = [round(value*1_000_000_000) for value in sorted(native)] == [500_000_000*index for index in range(len(pacing)+1)]
    checks['raw_to_projected_values_match'] = True
    checks['telemetry_hash_chain_valid'] = True
    artifacts['telemetry.jsonl'] = digest(trace)
    artifacts['diagnostic.json'] = digest(root/'diagnostic.json')
    artifacts['native-progress.json'] = digest(root/'native-run/progress.json')
    return {'case':name,'checks':checks,'passed':all(checks.values()),'artifact_sha256':artifacts,
            'raw_wrapper_status':diagnostic['status'],'native_status':progress['status'],
            'pacing_samples':len(pacing),'p95_ms':percentile(values,.95),'max_ms':max(values)}, native


def main():
    injection, injection_native = review('r11-timing-diagnostic-smoke-v3',wrapper_exit_bug=True)
    baseline, baseline_native = review('r11-timing-diagnostic-baseline-v4')
    identical = all(baseline_native.get(time) == values for time,values in injection_native.items())
    result = {'status':'PASS' if injection['passed'] and baseline['passed'] and identical else 'FAIL',
              'scope':'Instrument reliability and deterministic native-prefix check only. No tail-latency fix or formal timing qualification.',
              'cases':[injection,baseline], 'common_native_prefix_identical':identical,
              'common_native_samples':len(injection_native),
              'failure_disclosure':'v1/v2 startup failed; v3 raw wrapper status remains FAILED because original CLI exited0. Raw native v3 independently sealed successfully; v4 wrapper handles zero exit correctly. First reviewer wrongly compared native converted timestamps by exact float equality; its source and FAIL preserved. This review checks the original integer-nanosecond grid, without changing telemetry.',
              'reviewer_sha256':digest(Path(__file__))}
    (BASE/'r11-timing-diagnostics-independent-review-v2.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'status':result['status'],'checks':sum(len(case['checks']) for case in result['cases']),
                      'failed_checks':{case['case']:[key for key,value in case['checks'].items() if not value] for case in result['cases']},
                      'common_native_prefix_identical':identical,'common_native_samples':len(injection_native)}))
    assert result['status'] == 'PASS'


if __name__ == '__main__':
    main()
