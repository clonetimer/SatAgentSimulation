#!/usr/bin/env python3
"""用途：运行真实原生整星的墙钟持续试验，记录独立耐久证据，不自动恢复。
参数：--output-dir；--wall-seconds 默认 86430，短时运行明确标记为 smoke。
输出：完整原生工作区、哈希链观测、独立审计和进度；24h 证据仅供人工评估。
"""
from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))


SOURCE_FINGERPRINT_SCHEME = 'runtime-and-validation-v6'
SOURCE_DIRECTORIES = (
    'src/sat_sim/bsk_engine', 'src/sat_sim/interactive', 'src/subsystems',
    'src/integration', 'src/sat_sim/validation',
)
SOURCE_FILES = ('src/sat_sim/validation_outcome.py', 'src/sat_sim/run_bundle.py',
                'src/sat_sim/assertions.py',
                'src/sat_sim/effect_evidence.py', 'src/sat_sim/evidence/effect_evidence_v32.json',
                'src/sat_sim/thermal_environment.py', 'src/sat_sim/thermal_quadrature.py',
                'requirements-lock.txt')
APPLICATION_SOURCE_SCHEME = 'application-source-v1'


def application_source_manifest(root=None):
    """Bind exports, API, UI and packaged defaults as well as the physical core."""
    source_root = Path(root) if root is not None else ROOT
    folder = source_root/'src'
    if not folder.is_dir():
        raise FileNotFoundError('ENDURANCE_APPLICATION_SOURCE_MISSING: src')
    paths = {path for path in folder.rglob('*') if path.is_file()
             and '__pycache__' not in path.parts and path.suffix not in {'.pyc','.pyo'}}
    paths.update(source_root.glob('requirements*.txt'))
    paths.update(path for path in (source_root/'pyproject.toml',source_root/'constraints.txt') if path.is_file())
    return {'scheme':APPLICATION_SOURCE_SCHEME,'files':[
        {'path':path.relative_to(source_root).as_posix(),
         'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in sorted(paths)]}


def runtime_source_manifest(root=None):
    source_root = Path(root) if root is not None else ROOT
    paths = set()
    for directory in SOURCE_DIRECTORIES:
        folder = source_root/directory
        if not folder.is_dir():
            raise FileNotFoundError(f'ENDURANCE_SOURCE_DIRECTORY_MISSING: {directory}')
        paths.update(folder.rglob('*.py'))
    for relative in SOURCE_FILES:
        path = source_root/relative
        if not path.is_file():
            raise FileNotFoundError(f'ENDURANCE_VALIDATOR_SOURCE_MISSING: {relative}')
        paths.add(path)
    return {'scheme':SOURCE_FINGERPRINT_SCHEME, 'files':[
        {'path':path.relative_to(source_root).as_posix(),
         'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in sorted(paths)
    ]}


def runtime_source_fingerprint(manifest=None):
    payload = manifest if manifest is not None else runtime_source_manifest()
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def main():
    from fastapi.testclient import TestClient
    from sat_sim.api import create_app
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--wall-seconds',type=float,default=86430.)
    parser.add_argument('--initial-configuration',type=Path,
                        help='Strict local continuous initial-configuration JSON; copied and hashed before native start.')
    args = parser.parse_args()
    if args.wall_seconds<10:
        parser.error('--wall-seconds must be >= 10')
    from sat_sim.interactive.continuous_initial_config import ContinuousInitialConfiguration
    configuration_source = args.initial_configuration.resolve() if args.initial_configuration else None
    try:
        configuration_input = (configuration_source.read_bytes() if configuration_source else
            json.dumps({'orbit':{'environment_profile':'spice_wmm','epoch_utc':'2026-01-01T00:00:00Z'}}).encode('utf-8'))
        initial_model = ContinuousInitialConfiguration.model_validate_json(configuration_input)
        initial_configuration = initial_model.model_dump(exclude_unset=True)
    except (OSError,ValueError) as exc:
        parser.error(f'Invalid initial configuration: {exc}')
    output = args.output_dir.resolve()
    output.mkdir(parents=True,exist_ok=True)
    if (output/'progress.json').exists() or (output/'api').exists():
        parser.error('A fresh output directory is required; existing physics is never resumed')
    configuration_snapshot = output/'initial_configuration.input.json'
    configuration_snapshot.write_bytes(configuration_input)
    normalized_configuration = output/'initial_configuration.json'
    normalized_configuration.write_text(json.dumps(initial_configuration,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    os.environ['SAT_SIM_INTERACTIVE_ENABLED'] = '1'
    os.environ['SAT_SIM_SUPERVISED_NATIVE_RUNTIME'] = '1'
    source_before = runtime_source_manifest()
    provenance_before = output/'source_provenance.before.json'
    provenance_before.write_text(json.dumps(source_before,indent=2)+'\n',encoding='utf-8')
    application_before = application_source_manifest()
    application_provenance_before = output/'application_source.before.json'
    application_provenance_before.write_text(json.dumps(application_before,indent=2)+'\n',encoding='utf-8')
    report = {'schema_version':'sat-native-endurance-run.v3','status':'STARTING',
        'source_fingerprint_scheme':SOURCE_FINGERPRINT_SCHEME,
        'source_provenance_before_sha256':hashlib.sha256(provenance_before.read_bytes()).hexdigest(),
        'runtime_source_sha256_before':runtime_source_fingerprint(source_before),
        'application_source_scheme':APPLICATION_SOURCE_SCHEME,
        'application_source_sha256_before':runtime_source_fingerprint(application_before),
        'application_source_provenance_before_sha256':hashlib.sha256(application_provenance_before.read_bytes()).hexdigest(),
        'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'initial_configuration_source':str(configuration_source) if configuration_source else 'built_in_spice_wmm_default',
        'initial_configuration_input_sha256':hashlib.sha256(configuration_snapshot.read_bytes()).hexdigest(),
        'initial_configuration_normalized_sha256':hashlib.sha256(normalized_configuration.read_bytes()).hexdigest(),
        'requested_wall_seconds':args.wall_seconds, 'process_id':os.getpid(),
        'scope':'Real Basilisk through local API handlers; no physics protocol double; no auto-resume.',
        'started_at_utc':datetime.now(timezone.utc).isoformat(), 'manual_review_required':True}
    def save():
        staged = output/'progress.json.pending'
        staged.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        os.replace(staged,output/'progress.json')
    save()
    app = create_app(runs_root=output/'runs',artifacts_root=output/'api',embedded_worker=False,auth_mode='disabled')
    with TestClient(app) as client:
        def call(method,path,body=None):
            response = client.request(method,path,json=body)
            response.raise_for_status()
            return response.json()
        base = '/interactive/sessions/native-endurance'
        endurance = '/interactive/continuous/sessions/native-endurance/endurance'
        try:
            call('POST','/interactive/continuous/sessions',{
                'session_id':'native-endurance','quantum_s':.5,'rate':1.,'paced':True,
                'initial_horizon_s':60., 'initial_configuration':initial_configuration})
            applied_spec = json.loads((output/'api/interactive_sessions/native-endurance/session_spec.json').read_text(encoding='utf-8'))
            if applied_spec['session_spec']['task_spec']['parameters']['values'] != initial_model.native_values():
                raise RuntimeError('ENDURANCE_INITIAL_CONFIGURATION_NOT_APPLIED')
            report['applied_task_spec_sha256'] = applied_spec['task_spec_sha256']
            call('POST',base+'/control',{'action':'start'})
            # All qualifying samples must describe the same already-running
            # native instance. No periodic pause, restart or telemetry replay.
            call('POST',endurance+'/start',{'sample_interval_s':5. if args.wall_seconds>=86400 else 1.})
            started = time.monotonic()
            report['status'] = 'RUNNING'
            while True:
                report['session'] = call('GET',base)
                report['wall_observed_by_runner_s'] = time.monotonic()-started
                report['endurance'] = call('GET',endurance+'/status')
                report['observed_at_utc'] = datetime.now(timezone.utc).isoformat()
                save()
                if report['session']['state']!='RUNNING':
                    raise RuntimeError('NATIVE_ENDURANCE_SESSION_LEFT_RUNNING_STATE')
                remaining = args.wall_seconds-report['wall_observed_by_runner_s']
                if remaining<=0:
                    break
                time.sleep(min(10.,remaining))
            call('POST',base+'/control',{'action':'stop'})
            call('POST',endurance+'/stop',{})
            deadline = time.monotonic()+600
            while True:
                report['session'] = call('GET',base)
                if not report['session']['worker_alive']:
                    break
                if time.monotonic()>=deadline:
                    raise TimeoutError('ENDURANCE_ARCHIVE_DID_NOT_FINISH')
                time.sleep(.2)
            # Preserve independent uptime audit and the actual mission verdict
            # even when the pass-only archive gate correctly refuses a mission.
            report['audit'] = call('GET',endurance+'/audit')
            if report['session']['archive_status']=='SEALED':
                report['runtime_result'] = call('GET',base+'/runtime-result')
                report['runtime_result_evidence_scope'] = 'hash_verified_sealed_bundle'
            else:
                raw_result = output/'api/interactive_sessions/native-endurance/runtime_result.json'
                if raw_result.is_file():
                    raw_bytes = raw_result.read_bytes()
                    report['runtime_result'] = json.loads(raw_bytes)
                    report['runtime_result_sha256'] = hashlib.sha256(raw_bytes).hexdigest()
                report['runtime_result_evidence_scope'] = 'unsealed_local_diagnostic_only'

            report['qualification_scope'] = 'Runtime endurance and mission validity are separate; an uptime audit never overrides failed validation.'
            validation_path = output/'api/interactive_sessions/native-endurance/validation_outcome.json'
            if validation_path.is_file():
                report['validation_outcome'] = json.loads(validation_path.read_text(encoding='utf-8'))
            if report['session']['archive_status']!='SEALED' or report['session']['terminal_error']:
                raise RuntimeError('ENDURANCE_ARCHIVE_NOT_SEALED')
            if args.wall_seconds<86400:
                report['status'] = 'SMOKE_COMPLETE_NOT_24H'
            else:
                report['status'] = report['audit']['verdict']
        except BaseException as exc:
            report.update(status='INTERRUPTED' if isinstance(exc,KeyboardInterrupt) else 'FAILED',
                error={'type':type(exc).__name__,'message':str(exc)})
            # Best-effort stop of this test's own session, never replacement or replay.
            try:
                call('POST',base+'/control',{'action':'stop'})
            except Exception:
                pass
        finally:
            try:
                source_after = runtime_source_manifest()
                provenance_after = output/'source_provenance.after.json'
                provenance_after.write_text(json.dumps(source_after,indent=2)+'\n',encoding='utf-8')
                report['source_provenance_after_sha256'] = hashlib.sha256(provenance_after.read_bytes()).hexdigest()
                report['runtime_source_sha256_after'] = runtime_source_fingerprint(source_after)
                report['runtime_source_unchanged'] = report['runtime_source_sha256_before'] == report['runtime_source_sha256_after']
                application_after = application_source_manifest()
                application_provenance_after = output/'application_source.after.json'
                application_provenance_after.write_text(json.dumps(application_after,indent=2)+'\n',encoding='utf-8')
                report['application_source_sha256_after'] = runtime_source_fingerprint(application_after)
                report['application_source_provenance_after_sha256'] = hashlib.sha256(application_provenance_after.read_bytes()).hexdigest()
                report['application_source_unchanged'] = report['application_source_sha256_before'] == report['application_source_sha256_after']
                report['runner_sha256_after'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
                report['runner_source_unchanged'] = report['runner_sha256'] == report['runner_sha256_after']
                report['initial_configuration_unchanged'] = (
                    hashlib.sha256(configuration_snapshot.read_bytes()).hexdigest() == report['initial_configuration_input_sha256']
                    and hashlib.sha256(normalized_configuration.read_bytes()).hexdigest() == report['initial_configuration_normalized_sha256'])
            except Exception as exc:
                report.update(runtime_source_unchanged=False,runner_source_unchanged=False,application_source_unchanged=False,
                    initial_configuration_unchanged=False,
                    source_check_error={'type':type(exc).__name__,'message':str(exc)})
            if (not report['runtime_source_unchanged'] or not report['runner_source_unchanged']) and report['status'] in ('REVIEW_ELIGIBLE', 'SMOKE_COMPLETE_NOT_24H'):
                report.update(status='NOT_QUALIFIED', source_reason_code='RUNTIME_SOURCE_CHANGED_DURING_ENDURANCE')
            if not report['application_source_unchanged'] and report['status'] in ('REVIEW_ELIGIBLE', 'SMOKE_COMPLETE_NOT_24H'):
                report.update(status='NOT_QUALIFIED',source_reason_code='APPLICATION_SOURCE_CHANGED_DURING_ENDURANCE')
            if not report['initial_configuration_unchanged'] and report['status'] in ('REVIEW_ELIGIBLE', 'SMOKE_COMPLETE_NOT_24H'):
                report.update(status='NOT_QUALIFIED',source_reason_code='INITIAL_CONFIGURATION_CHANGED_DURING_ENDURANCE')
            report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
            save()
    print(json.dumps({key:report[key] for key in ('status','error','requested_wall_seconds') if key in report}))
    return 0 if report['status'] in ('REVIEW_ELIGIBLE','SMOKE_COMPLETE_NOT_24H') else 1


if __name__=='__main__':
    raise SystemExit(main())
