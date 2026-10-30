#!/usr/bin/env python3
"""用途：真实原生子进程异常后核验证据保留，再新建独立场景；不恢复旧物理状态。
参数：--output-dir 必须为全新目录。只终止此脚本新建并持有句柄的测试子进程。
输出：API 状态、原始证据摘要、诊断 ZIP 和新场景 SEALED 结果；不证明 24h 耐久性。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    from fastapi.testclient import TestClient
    from sat_sim.api import create_app
    from sat_sim.interactive.workspace import SessionWorkspace
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    os.environ['SAT_SIM_INTERACTIVE_ENABLED'] = '1'
    os.environ['SAT_SIM_SUPERVISED_NATIVE_RUNTIME'] = '1'
    report = {'schema_version':'sat-native-failure-recreation.v1', 'status':'RUNNING',
        'scope':'Real Basilisk via production API handlers; fresh-scene recreation, no old-state restoration.', 'checks':{}}
    checks = report['checks']
    app = create_app(runs_root=output/'runs', artifacts_root=output/'api',
                     embedded_worker=False, auth_mode='disabled')
    try:
        with TestClient(app) as client:
            def call(method, path, body=None):
                response = client.request(method, path, json=body)
                response.raise_for_status()
                return response.json()

            def create(session_id):
                return call('POST', '/interactive/continuous/sessions', {
                    'session_id':session_id, 'quantum_s':.5, 'paced':True, 'rate':1.,
                    'initial_configuration':{'orbit':{'environment_profile':'spice_wmm', 'epoch_utc':'2026-01-01T00:00:00Z'}}})

            def wait_until(session_id, predicate, timeout=40.):
                deadline = time.monotonic()+timeout
                while True:
                    value = call('GET', '/interactive/sessions/'+session_id)
                    if predicate(value):
                        return value
                    if time.monotonic() >= deadline:
                        raise TimeoutError('NATIVE_FAILURE_RECREATION_OBSERVATION_TIMEOUT: '+session_id)
                    time.sleep(.05)

            old_id, new_id = 'injected-native-failure', 'fresh-native-scene'
            create(old_id)
            old = app.state.interactive_manager.get(old_id)
            runtime = old.runtime
            if (runtime.__class__.__name__ != 'SupervisedContinuousNativeRuntime'
                    or runtime._spec.session_id != old_id or runtime._process is None):
                raise RuntimeError('FAULT_INJECTION_REQUIRES_OWN_NEW_NATIVE_PROCESS')
            owned_process = runtime._process
            old_nonce = runtime._nonce
            report['injected_child_pid'] = owned_process.pid
            checks['owned_native_child'] = owned_process.pid not in (None, os.getpid()) and owned_process.is_alive()
            if not checks['owned_native_child']:
                raise RuntimeError('FAULT_INJECTION_CHILD_NOT_LIVE')
            call('POST', f'/interactive/sessions/{old_id}/control', {'action':'start'})
            wait_until(old_id, lambda value:value['sim_time_s'] >= 1.)
            # Deliberate crash of this exact owned test child, not graceful stop.
            owned_process.kill()
            owned_process.join(timeout=5.)
            failed = wait_until(old_id, lambda value:value['state']=='FAILED' and not value['worker_alive'])
            report['failed_session'] = failed
            report['child_reap'] = runtime.last_reap
            checks['failure_detected'] = bool(failed.get('terminal_error'))
            checks['crashed_child_reaped'] = not owned_process.is_alive() and runtime.last_reap.get('exitcode') not in (None, 0)
            checks['no_implicit_resume_or_replay'] = runtime.last_reap.get('native_resume_supported') is False and runtime.last_reap.get('automatic_tc_replay_allowed') is False
            checks['old_resume_rejected'] = client.post(f'/interactive/sessions/{old_id}/control',json={'action':'resume'}).status_code == 409
            checks['old_id_overwrite_rejected'] = client.post('/interactive/continuous/sessions',json={'session_id':old_id}).status_code in (409,422)
            workspace = output/'api'/'interactive_sessions'/old_id
            inspection = SessionWorkspace.inspect(workspace)
            frames = sum(1 for _ in SessionWorkspace.iter_telemetry(workspace))
            checks['failed_original_evidence_valid'] = inspection.valid and inspection.state.value=='FAILED' and frames > 0
            original_hashes = {}
            for path in workspace.rglob('*'):
                if path.is_file():
                    if path.is_symlink() or not path.resolve().is_relative_to(workspace.resolve()):
                        raise ValueError('FAILURE_EVIDENCE_PATH_OUTSIDE_OWN_WORKSPACE')
                    original_hashes[path.relative_to(workspace).as_posix()] = sha256(path)
            with zipfile.ZipFile(output/'failed-original-evidence.zip','w',zipfile.ZIP_DEFLATED) as archive:
                for relative in sorted(original_hashes):
                    archive.write(workspace/relative,relative)
            report['failed_evidence_sha256'] = original_hashes
            report['diagnostic_zip_sha256'] = sha256(output/'failed-original-evidence.zip')

            ready = create(new_id)
            fresh = app.state.interactive_manager.get(new_id)
            checks['fresh_scene_starts_at_zero'] = ready['sim_time_s'] == 0.
            checks['fresh_native_generation'] = fresh.runtime._nonce != old_nonce and fresh.runtime._process is not owned_process
            call('POST',f'/interactive/sessions/{new_id}/control',{'action':'start'})
            wait_until(new_id,lambda value:value['sim_time_s'] >= 1.)
            call('POST',f'/interactive/sessions/{new_id}/control',{'action':'stop'})
            completed = wait_until(new_id,lambda value:not value['worker_alive'])
            report['fresh_session'] = completed
            result = call('GET',f'/interactive/sessions/{new_id}/runtime-result')
            report['fresh_runtime_result'] = result
            summary = result['result']['summary']
            checks['fresh_scene_sealed'] = completed['state']=='COMPLETED' and completed['archive_status']=='SEALED'
            checks['fresh_actual_native_environment'] = summary.get('runtime_truth_status')=='instantiated_connected_recorded_unified' and summary['orbital_environment']['profile']=='spice_wmm'
            checks['failed_evidence_unchanged'] = all(sha256(workspace/path)==digest for path,digest in original_hashes.items())
            report['status'] = 'PASS' if all(checks.values()) else 'FAIL'
    except Exception as exc:
        report.update(status='FAIL',error={'type':type(exc).__name__,'message':str(exc)})
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'checks':checks,'error':report.get('error')}))
    return int(report['status'] != 'PASS')


if __name__ == '__main__':
    raise SystemExit(main())
