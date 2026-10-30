"""Owned-process diagnostic; never attach to or modify the endurance process.

Copies the frozen application and runs its original endurance driver. Python
profiling records inclusive spans and original PaceObservation values in memory;
this is diagnostic, not a formal timing or crash-durability qualification.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]


class Recorder:
    LIMIT = 1_000_000
    SELECTED = {
        'workspace.py': {'append_telemetry', 'append_state', 'append_command_ack',
                         'finalize_telemetry', '_canonical'},
        'manager.py': {'_advance', '_write_heartbeat', '_emit_ack'},
        'clock.py': {'pace_to'},
        'telemetry_bus.py': {'publish'},
        'telemetry_segments.py': {'roll_active'},
        'supervised_native_runtime.py': {'advance_to', '_rpc'},
        'unified_runtime.py': {'advance_to'},
        'runtime.py': {'advance_to'},
    }

    def __init__(self):
        self.events = []
        self.pace = []
        self.pending = threading.local()
        self.overflow = False

    def append(self, row):
        if len(self.events) >= self.LIMIT:
            self.overflow = True
            return
        self.events.append(row)

    def profile(self, frame, event, arg):
        filename = frame.f_code.co_filename.replace('\\', '/')
        if '/sat_sim/interactive/' not in filename:
            return
        basename = filename.rsplit('/', 1)[-1]
        name = frame.f_code.co_name
        selected = name in self.SELECTED.get(basename, ())
        if not hasattr(self.pending, 'spans'):
            self.pending.spans = {}
            self.pending.c_spans = {}
        if event == 'call' and selected:
            self.pending.spans[id(frame)] = time.perf_counter_ns()
        elif event == 'return' and selected:
            end = time.perf_counter_ns()
            start = self.pending.spans.pop(id(frame), None)
            if start is not None:
                row = {'kind': 'python', 'stage': f'{basename}:{name}',
                       'start_ns': start, 'end_ns': end,
                       'thread_id': threading.get_native_id()}
                if basename == 'clock.py' and name == 'pace_to' and arg is not None:
                    observation = asdict(arg)
                    self.pace.append(observation)
                    row['pace'] = observation
                self.append(row)
        elif event in {'c_call', 'c_return', 'c_exception'}:
            function = getattr(arg, '__name__', '')
            if basename not in {'workspace.py', 'telemetry_segments.py'}:
                return
            if function not in {'fsync', 'write', 'flush', '__enter__', '__exit__',
                                'acquire', 'release'}:
                return
            owner = getattr(arg, '__self__', None)
            owner_type = type(owner).__name__
            if function in {'__enter__', '__exit__', 'acquire', 'release'} and 'lock' not in owner_type.lower():
                return
            key = (id(frame), function)
            if event == 'c_call':
                self.pending.c_spans[key] = time.perf_counter_ns()
            else:
                end = time.perf_counter_ns()
                start = self.pending.c_spans.pop(key, None)
                if start is not None:
                    self.append({'kind': 'c', 'stage': f'{basename}:{name}/{function}',
                                 'owner_type': owner_type, 'start_ns': start,
                                 'end_ns': end, 'exception': event == 'c_exception',
                                 'thread_id': threading.get_native_id()})

    @staticmethod
    def percentile(values, percentage):
        if not values:
            return None
        ordered = sorted(values)
        position = (len(ordered) - 1) * percentage / 100
        low = int(position)
        high = min(low + 1, len(ordered) - 1)
        return ordered[low] + (ordered[high] - ordered[low]) * (position - low)

    def summary(self):
        groups = defaultdict(list)
        for row in self.events:
            groups[row['stage']].append((row['end_ns'] - row['start_ns']) / 1e6)
        stages = {name: {'count': len(values), 'p95_ms': self.percentile(values, 95),
                         'max_ms': max(values), 'total_ms': sum(values)}
                  for name, values in sorted(groups.items())}
        drift = [abs(row['drift_s']) * 1000 for row in self.pace]
        windows = []
        if self.pace:
            first_wall = self.pace[0]['wall_time_s']
            last_wall = self.pace[-1]['wall_time_s']
            count = int((last_wall-first_wall)/1800)
            for index in range(count):
                start = first_wall+1800*index
                values = [abs(row['drift_s'])*1000 for row in self.pace
                          if start <= row['wall_time_s'] < start+1800]
                windows.append({'start_monotonic_s': start, 'duration_s': 1800,
                                'samples': len(values), 'abs_drift_p95_ms': self.percentile(values, 95),
                                'abs_drift_p99_ms': self.percentile(values, 99),
                                'abs_drift_max_ms': max(values) if values else None})
        return {'events': len(self.events), 'overflow': self.overflow, 'stages': stages,
                'pacing_samples': len(drift), 'abs_drift_p95_ms': self.percentile(drift, 95),
                'abs_drift_p99_ms': self.percentile(drift, 99),
                'abs_drift_max_ms': max(drift) if drift else None,
                'complete_30min_windows': windows,
                'scope': 'Inclusive spans overlap. Instrumentation adds overhead. Short-run samples do not qualify a 30-minute or 24-hour window.'}


class DiagnosticLock:
    """Same underlying blocking lock, with explicit acquire/hold spans."""
    def __init__(self, lock, recorder):
        self.lock = lock
        self.recorder = recorder
        self.holds = {}

    def __enter__(self):
        start = time.perf_counter_ns()
        self.lock.acquire()
        acquired = time.perf_counter_ns()
        thread = threading.get_native_id()
        self.holds[thread] = acquired
        self.recorder.append({'kind': 'lock', 'stage': 'workspace.lock_wait',
                              'start_ns': start, 'end_ns': acquired, 'thread_id': thread})
        return self

    def __exit__(self, exc_type, exc, traceback):
        thread = threading.get_native_id()
        start = self.holds.pop(thread)
        end = time.perf_counter_ns()
        self.lock.release()
        self.recorder.append({'kind': 'lock', 'stage': 'workspace.lock_hold',
                              'start_ns': start, 'end_ns': end, 'thread_id': thread})
        return False


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--wall-seconds', type=float, default=60)
    parser.add_argument('--inject-fsync-delay-s', type=float, default=0)
    args = parser.parse_args()
    if args.wall_seconds < 10 or args.inject_fsync_delay_s < 0:
        parser.error('wall seconds >=10 and injected delay >=0 required')
    output = args.output_dir.resolve()
    if not output.is_relative_to(ROOT/'reports/local_requirements_completion'):
        parser.error('output must remain in the ignored local reports workspace')
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(Path(__file__), output/'diagnostic_source.py')
    copy = output/'candidate'
    copy.mkdir()
    source_module = runpy.run_path(str(ROOT/'scripts/run_native_endurance.py'))
    manifest = source_module['application_source_manifest']()
    fingerprint = source_module['runtime_source_fingerprint']
    before = fingerprint(manifest)
    for entry in manifest['files']:
        target = copy/entry['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT/entry['path'], target)
        assert sha(target) == entry['sha256']
    shutil.copytree(ROOT/'configs', copy/'configs')
    (copy/'scripts').mkdir()
    driver = copy/'scripts/run_native_endurance.py'
    shutil.copyfile(ROOT/'scripts/run_native_endurance.py', driver)
    assert fingerprint(source_module['application_source_manifest'](copy)) == before
    import psutil
    current = psutil.Process()
    scheduling = {'pid': current.pid, 'creation': current.create_time(),
                  'original_affinity': current.cpu_affinity()}
    if os.name == 'nt':
        current.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    allowed = current.cpu_affinity()
    candidate_cores = [number for number in allowed if number >= 12]
    if candidate_cores:
        current.cpu_affinity(candidate_cores)
    scheduling['selected_affinity'] = current.cpu_affinity()
    scheduling['scope'] = 'Only this diagnostic process and newly spawned children; not exclusive physical resource isolation.'
    sys.path.insert(0, str(copy/'src'))
    # Source copies have no deployment assets. Select the existing audited
    # read-only asset root explicitly; do not downgrade the environment profile.
    asset_root = ROOT/'third_party'
    os.environ['SAT_SIM_ASSET_ROOT'] = str(asset_root)
    asset_paths = [asset_root/'gravity/GGM03S-J2-only.txt']
    asset_paths.extend(sorted((asset_root/'spice').glob('*')))
    asset_paths.extend(sorted((asset_root/'wmm').glob('*')))
    asset_manifest = [{'path': str(path), 'sha256': sha(path)}
                      for path in asset_paths if path.is_file()]
    from sat_sim.interactive import workspace
    assert Path(workspace.__file__).resolve().is_relative_to(copy)
    from fastapi.testclient import TestClient
    original_request = TestClient.request
    failed_responses = []

    def diagnostic_request(self, *call_args, **kwargs):
        response = original_request(self, *call_args, **kwargs)
        if response.status_code >= 400:
            failed_responses.append({'url': str(response.url), 'status': response.status_code,
                                     'body': response.text})
        return response

    TestClient.request = diagnostic_request
    recorder = Recorder()
    original_workspace_init = workspace.SessionWorkspace.__init__

    def diagnostic_workspace_init(self, *init_args, **kwargs):
        original_workspace_init(self, *init_args, **kwargs)
        self._lock = DiagnosticLock(self._lock, recorder)

    workspace.SessionWorkspace.__init__ = diagnostic_workspace_init
    injection = []
    real_fsync = os.fsync

    def diagnostic_fsync(fd):
        caller = sys._getframe(1)
        if (not injection and caller.f_code.co_name == 'append_telemetry'
                and args.inject_fsync_delay_s > 0):
            start = time.perf_counter_ns()
            injection.append({'start_ns': start, 'requested_s': args.inject_fsync_delay_s})
            time.sleep(args.inject_fsync_delay_s)
            injection[0]['end_ns'] = time.perf_counter_ns()
            recorder.append({'kind': 'injected', 'stage': 'diagnostic.injected_pre_fsync_sleep',
                             'start_ns': start, 'end_ns': injection[0]['end_ns'],
                             'thread_id': threading.get_native_id()})
        start = time.perf_counter_ns()
        try:
            return real_fsync(fd)
        finally:
            recorder.append({'kind': 'wrapped_c', 'stage': f'{Path(caller.f_code.co_filename).name}:{caller.f_code.co_name}/real_fsync',
                             'start_ns': start, 'end_ns': time.perf_counter_ns(),
                             'thread_id': threading.get_native_id()})

    report = {'status': 'RUNNING', 'scope': 'Isolated native diagnostic, not production qualification.',
              'started_utc': datetime.now(timezone.utc).isoformat(),
              'application_sha256': before, 'driver_sha256': sha(driver),
              'diagnostic_sha256': sha(Path(__file__)), 'scheduling': scheduling,
              'source_copy_byte_identical': True, 'requested_wall_s': args.wall_seconds,
              'read_only_shared_assets': asset_manifest,
              'injected_delay_s': args.inject_fsync_delay_s}
    state = output/'diagnostic.json'
    state.write_text(json.dumps(report, indent=2), encoding='utf-8')
    try:
        os.fsync = diagnostic_fsync
        sys.setprofile(recorder.profile)
        threading.setprofile(recorder.profile)
        sys.argv = [str(driver), '--output-dir', str(output/'native-run'),
                    '--wall-seconds', str(args.wall_seconds), '--initial-configuration',
                    str(copy/'configs/interactive/generic_leo_thermal_link_candidate.json')]
        try:
            runpy.run_path(str(driver), run_name='__main__')
        except SystemExit as exc:
            # The unchanged CLI deliberately exits even after a successful
            # smoke. Preserve nonzero exit codes; then inspect its raw status.
            if exc.code not in (None, 0):
                raise
        progress = json.loads((output/'native-run/progress.json').read_text(encoding='utf-8'))
        report['native_status'] = progress['status']
        report['status'] = 'DIAGNOSTIC_CAPTURED_NOT_QUALIFIED'
        if progress['status'] not in {'SMOKE_COMPLETE_NOT_24H', 'REVIEW_ELIGIBLE'}:
            raise RuntimeError('DIAGNOSTIC_NATIVE_RUN_FAILED')
    except BaseException as exc:
        report.update(status='FAILED', error={'type': type(exc).__name__, 'message': str(exc)})
        raise
    finally:
        sys.setprofile(None)
        threading.setprofile(None)
        os.fsync = real_fsync
        TestClient.request = original_request
        workspace.SessionWorkspace.__init__ = original_workspace_init
        report['failed_responses'] = failed_responses
        report['injection'] = injection
        report['summary'] = recorder.summary()
        report['root_application_unchanged'] = fingerprint(source_module['application_source_manifest']()) == before
        report['candidate_application_unchanged'] = fingerprint(source_module['application_source_manifest'](copy)) == before
        report['shared_assets_unchanged'] = all(sha(Path(item['path'])) == item['sha256'] for item in asset_manifest)
        report['diagnostic_source_unchanged'] = sha(Path(__file__)) == report['diagnostic_sha256']
        if not all(report[key] for key in ['root_application_unchanged', 'candidate_application_unchanged',
                                          'shared_assets_unchanged', 'diagnostic_source_unchanged']):
            report['status'] = 'INCOMPLETE_SOURCE_OR_ASSET_CHANGED'
        report['diagnostic_source_unchanged'] = sha(Path(__file__)) == report['diagnostic_sha256']
        if not all(report[key] for key in ['root_application_unchanged', 'candidate_application_unchanged',
                                          'shared_assets_unchanged', 'diagnostic_source_unchanged']):
            report['status'] = 'INCOMPLETE_SOURCE_OR_ASSET_CHANGED'
        if recorder.overflow:
            report['status'] = 'INCOMPLETE_PROFILE_OVERFLOW'
        for filename, rows in [('timing.jsonl', recorder.events), ('pacing.jsonl', recorder.pace)]:
            target = output/filename
            with target.open('x', encoding='utf-8') as stream:
                for row in rows:
                    stream.write(json.dumps(row, sort_keys=True)+'\n')
            report[filename+'_sha256'] = sha(target)
        state.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'status': report['status'], 'path': str(state), 'summary': report['summary']}), flush=True)


if __name__ == '__main__':
    main()
