"""Attribute recorded drift episodes to measured call sites, not device causes.

Reads completed diagnose_native_timing output without altering raw evidence.
Inclusive wall-time spans include OS scheduling; they are not CPU-time proofs.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    offset = (len(ordered)-1)*fraction
    low = int(offset)
    high = min(low+1, len(ordered)-1)
    return ordered[low]+(ordered[high]-ordered[low])*(offset-low)


def union_duration_ns(intervals: list[tuple[int, int]]) -> int:
    """Coverage of wall intervals; nested/overlapping spans count once."""
    total = 0
    end = None
    for start, stop in sorted(intervals):
        if end is None or start > end:
            total += stop-start
            end = stop
        elif stop > end:
            total += stop-end
            end = stop
    return total


def analyze(folder: Path, threshold_ms: float) -> dict:
    diagnostic_path = folder/'diagnostic.json'
    progress_path = folder/'native-run/progress.json'
    diagnostic = json.loads(diagnostic_path.read_text(encoding='utf-8'))
    progress = json.loads(progress_path.read_text(encoding='utf-8'))
    if progress['status'] not in {'SMOKE_COMPLETE_NOT_24H', 'REVIEW_ELIGIBLE'}:
        raise ValueError('COMPLETED_NATIVE_RECORD_REQUIRED')
    wrapper_bug = diagnostic.get('error') == {'type':'SystemExit','message':'0'}
    if diagnostic['status'] != 'DIAGNOSTIC_CAPTURED_NOT_QUALIFIED' and not (
            diagnostic['status'] == 'FAILED' and wrapper_bug):
        raise ValueError('COMPLETE_DIAGNOSTIC_REQUIRED')
    if diagnostic['summary']['overflow']:
        raise ValueError('INCOMPLETE_PROFILE_OVERFLOW')
    for key in ['root_application_unchanged', 'candidate_application_unchanged', 'shared_assets_unchanged']:
        if diagnostic[key] is not True:
            raise ValueError(f'SOURCE_OR_ASSET_CHANGED:{key}')
    if diagnostic.get('diagnostic_source_unchanged') is False:
        raise ValueError('DIAGNOSTIC_SOURCE_CHANGED')
    files = {}
    for name in ['timing.jsonl', 'pacing.jsonl']:
        actual = digest(folder/name)
        if actual != diagnostic[name+'_sha256']:
            raise ValueError(f'PROFILE_HASH_MISMATCH:{name}')
        files[name] = actual
    events = [json.loads(line) for line in (folder/'timing.jsonl').read_text(encoding='utf-8').splitlines()]
    pacing = [json.loads(line) for line in (folder/'pacing.jsonl').read_text(encoding='utf-8').splitlines()]
    if len(events) != diagnostic['summary']['events']:
        raise ValueError('EVENT_COUNT_MISMATCH')
    if not pacing or len(pacing) != progress['session']['pace_observation_count']:
        raise ValueError('PACING_COUNT_MISMATCH')
    if any(not isinstance(row['start_ns'], int) or not isinstance(row['end_ns'], int)
           or row['end_ns'] < row['start_ns'] for row in events):
        raise ValueError('INVALID_EVENT_SPAN')
    for index, row in enumerate(pacing):
        if any(not math.isfinite(row[key]) for key in ['wall_time_s','sim_time_s','drift_s']):
            raise ValueError('INVALID_PACING_VALUE')
        if index and (row['wall_time_s'] < pacing[index-1]['wall_time_s']
                      or row['sim_time_s'] <= pacing[index-1]['sim_time_s']):
            raise ValueError('PACING_ORDER_INVALID')
    clock = sorted((row for row in events if row['stage']=='clock.py:pace_to'),key=lambda row:row['start_ns'])
    advance = sorted((row for row in events if row['stage']=='manager.py:_advance'),key=lambda row:row['start_ns'])
    if len(clock) != len(pacing) or len(advance) != len(pacing):
        raise ValueError('ONE_ADVANCE_PER_PACE_REQUIRED')
    for index, (tick, step, observation) in enumerate(zip(clock,advance,pacing)):
        if tick.get('pace') != observation or tick['thread_id'] != step['thread_id']:
            raise ValueError('PACE_ADVANCE_BINDING_INVALID')
        if step['end_ns'] > tick['start_ns'] or (index and step['start_ns'] < clock[index-1]['end_ns']):
            raise ValueError('PACE_ADVANCE_ORDER_INVALID')
        # The clock reading is made inside pace_to. A long deschedule after
        # that reading is valid evidence, not a reason to reject the trace.
        observed_ns = observation['wall_time_s']*1e9
        if not tick['start_ns']-1000 <= observed_ns <= tick['end_ns']+1000:
            raise ValueError('CLOCK_OBSERVATION_OUTSIDE_PROFILE_SPAN')
    magnitudes = [abs(row['drift_s'])*1000 for row in pacing]
    if max(magnitudes) != progress['session']['max_abs_drift_s']*1000:
        raise ValueError('MAXIMUM_DRIFT_MISMATCH')
    groups = []
    for index, value in enumerate(magnitudes):
        if value >= threshold_ms:
            if groups and index == groups[-1][-1]+1:
                groups[-1].append(index)
            else:
                groups.append([index])
    episodes = []
    for indexes in groups:
        first = indexes[0]
        peak = max(indexes,key=lambda index:magnitudes[index])
        start = clock[first-1]['end_ns'] if first else advance[first]['start_ns']
        end = clock[peak]['end_ns']
        worker = clock[peak]['thread_id']
        operations = []
        concurrent = []
        for row in events:
            # Explicit blocking call sites only, excluding inclusive parents.
            stage = row['stage']
            eligible = (stage.endswith(('/real_fsync','/write','/flush'))
                        or stage in {'workspace.lock_wait','diagnostic.injected_pre_fsync_sleep',
                                     'supervised_native_runtime.py:advance_to'})
            if not eligible or row['end_ns'] <= start or row['start_ns'] >= end:
                continue
            overlap = (min(end,row['end_ns'])-max(start,row['start_ns']))/1e6
            item = {'stage':stage,'thread_id':row['thread_id'],
                    'span_ms':(row['end_ns']-row['start_ns'])/1e6,'overlap_ms':overlap,
                    'start_ns':row['start_ns'],'end_ns':row['end_ns']}
            (operations if row['thread_id']==worker else concurrent).append(item)
        operations.sort(key=lambda row:row['overlap_ms'],reverse=True)
        concurrent.sort(key=lambda row:row['overlap_ms'],reverse=True)
        observation = pacing[peak]
        step = advance[peak]
        covered = []
        for row in events:
            if row['thread_id'] != worker or row['end_ns']<=step['start_ns'] or row['start_ns']>=step['end_ns']:
                continue
            stage = row['stage']
            if (stage.endswith(('/real_fsync','/write','/flush'))
                    or stage in {'workspace.lock_wait','workspace.py:_canonical',
                                 'telemetry_bus.py:publish','diagnostic.injected_pre_fsync_sleep',
                                 'supervised_native_runtime.py:advance_to'}):
                covered.append((max(step['start_ns'],row['start_ns']),min(step['end_ns'],row['end_ns'])))
        measured_ns = union_duration_ns(covered)
        advance_ns = step['end_ns']-step['start_ns']
        gap_ms = (step['start_ns']-clock[peak-1]['end_ns'])/1e6 if peak else None
        overshoot_ms = (observation['actual_sleep_s']-observation['requested_sleep_s'])*1000
        if overshoot_ms >= threshold_ms:
            location = 'CLOCK_SLEEP_OVERRUN_OBSERVED'
        elif operations and operations[0]['overlap_ms'] >= threshold_ms:
            location = 'RECORDED_WORKER_CALL_SITE_OVERRUN_OBSERVED'
        elif gap_ms is not None and gap_ms >= threshold_ms:
            location = 'BETWEEN_PACE_RETURN_AND_ADVANCE_ENTRY_OVERRUN_OBSERVED'
        else:
            location = 'CAUSE_NOT_LOCALIZED_OR_PROPAGATED_LATENESS'
        episodes.append({'first_sim_time_s':pacing[first]['sim_time_s'],
                         'last_sim_time_s':pacing[indexes[-1]]['sim_time_s'],
                         'samples':len(indexes),'peak_sim_time_s':observation['sim_time_s'],
                         'peak_signed_drift_ms':observation['drift_s']*1000,
                         'window_start_ns':start,'window_end_ns':end,
                         'peak_step_advance_ms':advance_ns/1e6,
                         'peak_step_measured_site_union_ms':measured_ns/1e6,
                         'peak_step_uninstrumented_residual_ms':(advance_ns-measured_ns)/1e6,
                         'peak_step_gap_from_previous_pace_ms':gap_ms,
                         'peak_sleep_overshoot_ms':overshoot_ms,
                         'observed_location_not_root_cause':location,
                         'worker_blocking_sites':operations[:12],
                         'concurrent_activity_not_proof_of_causality':concurrent[:8]})
    stage_durations = defaultdict(list)
    for row in events:
        stage_durations[row['stage']].append((row['end_ns']-row['start_ns'])/1e6)
    windows = []
    origin = pacing[0]['wall_time_s']
    count = int((pacing[-1]['wall_time_s']-origin)/1800)
    for index in range(count):
        start = origin+index*1800
        values = [abs(row['drift_s'])*1000 for row in pacing if start <= row['wall_time_s'] < start+1800]
        windows.append({'start_monotonic_s':start,'duration_s':1800,'samples':len(values),
                        'p95_ms':percentile(values,.95),'p99_ms':percentile(values,.99),
                        'max_ms':max(values) if values else None})
    files.update({'diagnostic.json':digest(diagnostic_path),'native-run/progress.json':digest(progress_path)})
    return {'status':'ANALYZED_NOT_FORMAL_QUALIFICATION',
            'scope':'Recorded call-site wall times include scheduling. An fsync span alone cannot establish physical disk, OS, GIL or device root cause. Inclusive spans overlap; do not sum them. Episode threshold is a locator, not a new maximum-drift acceptance criterion.',
            'raw_wrapper_status':diagnostic['status'],'known_zero_exit_wrapper_bug':wrapper_bug,
            'artifact_sha256':files,'application':diagnostic['application_sha256'],
            'pacing_samples':len(pacing),'threshold_ms':threshold_ms,
            'all_samples_p95_ms':percentile(magnitudes,.95),'all_samples_p99_ms':percentile(magnitudes,.99),
            'maximum_abs_drift_ms':max(magnitudes),'complete_30min_windows':windows,
            'episodes':episodes,'stage_statistics':{name:{'count':len(values),
              'p95_ms':percentile(values,.95),'max_ms':max(values)} for name,values in sorted(stage_durations.items())}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('diagnostic_directory',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--threshold-ms',type=float,default=200)
    args = parser.parse_args()
    if not math.isfinite(args.threshold_ms) or args.threshold_ms<=0:
        parser.error('positive finite threshold required')
    if args.output.exists():
        parser.error('fresh analysis output required; never overwrite evidence')
    result = analyze(args.diagnostic_directory.resolve(),args.threshold_ms)
    result['analyzer_sha256'] = digest(Path(__file__))
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':result['status'],'samples':result['pacing_samples'],
                      'episodes':len(result['episodes']),'complete_30min_windows':len(result['complete_30min_windows'])}))


if __name__=='__main__':
    main()
