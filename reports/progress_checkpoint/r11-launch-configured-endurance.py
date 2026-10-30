"""Check prerequisites, then explicitly --start fresh configured10s and24h runs.

No historical duration credit, automatic resume, model substitution or release
approval. Production and agent visual review must be terminal and source-bound.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import time

import psutil

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'reports/local_requirements_completion'
OUT = BASE / 'hardware-bounded-r11-endurance'
CORE = '59d0f3613c8f21e267d8c2fd7886bb3af6a4be71a32f521d3e8b4646133a9cbe'
APP = '4cce6d46062a27bc310cc168c1d9587a458c56b4692bd60cf2c60548c2698f82'
INPUT = ROOT / 'configs/interactive/generic_leo_thermal_link_candidate.json'
PYTHON = ROOT / '.venv/Scripts/python.exe'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prerequisites():
    module = runpy.run_path(str(ROOT / 'scripts/run_native_endurance.py'))
    fp = module['runtime_source_fingerprint']
    checks = {'selected_current_core': fp() == CORE,
              'selected_current_application': fp(module['application_source_manifest']()) == APP,
              'configured_input_matches_reviewed_bytes': sha(INPUT) == '12c50af31f2f8a5764d81a6aceaecc7aa8fab0ddb94087a38a725d5e0e57c7f4',
              'configured_runner_matches_reviewed_bytes': sha(ROOT / 'scripts/run_native_endurance.py') == '58ac156f129e6d00457f87ae1791a782b7e1cb469903baf62dfb19c92e0018b0'}
    qa_path = BASE / 'hardware-bounded-r11-qualification/state.json'
    qa = read(qa_path) if qa_path.is_file() else {}
    checks['production_qualification_terminal_pass'] = qa.get('status') == 'CHECKS_PASS_INDEPENDENT_AND_VISUAL_REVIEW_PENDING'
    review_path = BASE / 'hardware-bounded-r11-independent-review.json'
    checks['agent_visual_and_independent_review_available'] = review_path.is_file()
    if review_path.is_file():
        review = read(review_path)
        checks['production_review_bound_and_passed'] = (
            review['status'] == 'INDEPENDENT_PRODUCTION_QUALIFICATION_AND_VISUAL_REVIEW_PASS'
            and review.get('stage') == 'all'
            and review['application'] == APP and review['core'] == CORE
            and review['qualification_state_sha256'] == sha(qa_path)
            and review.get('review_driver_sha256') == sha(BASE / 'review_hardware_bounded_qualification_v2.py')
            and bool(review['checks']) and all(review['checks'].values())
            and bool(review['rendered_image_sha256'])
            and bool(review['source_artifact_sha256'])
            and all(sha(ROOT / name) == digest for name, digest in review['rendered_image_sha256'].items())
            and all(sha(ROOT / name) == digest for name, digest in review['source_artifact_sha256'].items()))
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', action='store_true')
    args = parser.parse_args()
    checks = prerequisites()
    if not args.start:
        print(json.dumps({'status': 'READY_FOR_EXPLICIT_START' if all(checks.values()) else 'PREREQUISITES_PENDING',
                          'checks': checks, 'new_native_started': False}, indent=2))
        return 0 if all(checks.values()) else 2
    if not all(checks.values()):
        raise RuntimeError('ENDURANCE_PREREQUISITES_NOT_MET')
    OUT.mkdir(exist_ok=False)
    (OUT / 'process-temp').mkdir()
    process = psutil.Process()
    state = {'status': 'STARTING_CONFIGURED_PREFLIGHT', 'started_at_utc': datetime.now(timezone.utc).isoformat(),
             'pid': process.pid, 'created': process.create_time(), 'command': process.cmdline(),
             'preflight_checks': checks, 'application': APP, 'core': CORE, 'steps': [],
             'driver_sha256': sha(Path(__file__)), 'input_sha256': sha(INPUT),
             'runner_sha256': sha(ROOT / 'scripts/run_native_endurance.py'),
             'prior_duration_credit_s': 0, 'native_resume_supported': False, 'automatic_release_approval': False}
    env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONPATH': str(ROOT / 'src'),
           'TEMP': str(OUT / 'process-temp'), 'TMP': str(OUT / 'process-temp')}

    def save():
        staged = OUT / 'state.json.pending'
        staged.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')
        staged.replace(OUT / 'state.json')

    def run(name, command):
        if not all(prerequisites().values()) or sha(Path(__file__)) != state['driver_sha256']:
            raise RuntimeError('SOURCE_OR_REVIEW_CHANGED_DURING_ENDURANCE_CAMPAIGN')
        state['status'] = name
        row = {'name': name, 'command': [str(p) for p in command], 'started_at_utc': datetime.now(timezone.utc).isoformat()}
        state['steps'].append(row)
        save()
        with (OUT / (name + '.log')).open('xb') as log:
            child = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            row.update(pid=child.pid, created=psutil.Process(child.pid).create_time())
            save()
            while child.poll() is None:
                current = psutil.Process(child.pid)
                if current.create_time() != row['created']:
                    raise RuntimeError('ENDURANCE_CHILD_IDENTITY_CHANGED')
                row['last_verified_alive_at_utc'] = datetime.now(timezone.utc).isoformat()
                save()
                time.sleep(10.)
            row['returncode'] = child.returncode
        row['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
        save()
        if not all(prerequisites().values()) or sha(Path(__file__)) != state['driver_sha256']:
            raise RuntimeError('SOURCE_OR_REVIEW_CHANGED_DURING_ENDURANCE_STEP')
        return child.returncode

    save()
    try:
        runner = [PYTHON, ROOT / 'scripts/run_native_endurance.py', '--initial-configuration', INPUT]
        if run('CONFIGURED10S_PREFLIGHT', [*runner, '--output-dir', OUT / 'smoke', '--wall-seconds', '10']) != 0:
            raise RuntimeError('CONFIGURED_PREFLIGHT_FAILED')
        smoke = read(OUT / 'smoke/progress.json')
        if smoke['status'] != 'SMOKE_COMPLETE_NOT_24H' or smoke['validation_outcome']['result'] != 'PASS':
            raise RuntimeError('CONFIGURED_PREFLIGHT_NOT_VALIDATED')
        auditor = BASE / 'review_completed_hardware_bounded_r11_endurance.py'
        # A real short run must fail qualification for duration, while every
        # configuration, source, mission and artifact check passes.
        negative_exit = run('SHORT_NEGATIVE_AUDITOR', [PYTHON, auditor, '--run-dir', OUT / 'smoke', '--output', OUT / 'smoke-independent-review.json'])
        negative = read(OUT / 'smoke-independent-review.json')
        duration = {'driver_requested_at_least24h', 'driver_observed_at_least24h', 'original_driver_review_eligible',
                    'independent_observed_running24h', 'independent_audit_review_eligible'}
        if negative_exit != 1 or negative['status'] != 'NOT_QUALIFIED' or {k for k, v in negative['checks'].items() if not v} != duration:
            raise RuntimeError('SHORT_AUDITOR_DID_NOT_REJECT_ONLY_DURATION_CONDITIONS')
        state['duration_inheritance'] = 'NONE; separate smoke and historic instances contribute zero time.'
        save()
        if run('SINGLE_INSTANCE_CONFIGURED24H', [*runner, '--output-dir', OUT / 'endurance-24h', '--wall-seconds', '86430']) != 0:
            raise RuntimeError('CONFIGURED24H_ORIGINAL_RUNNER_FAILED_REVIEW_REQUIRED')
        if run('INDEPENDENT24H_REVIEW', [PYTHON, auditor, '--run-dir', OUT / 'endurance-24h', '--output', OUT / 'independent24h-review.json']) != 0:
            raise RuntimeError('CONFIGURED24H_INDEPENDENT_REVIEW_NOT_QUALIFIED')
        state['status'] = 'TECHNICAL24H_EVIDENCE_COMPLETE_REQUIRES_AGENT_ACCEPTANCE_REVIEW'
    except BaseException as exc:
        state.update(status='FAILED_REQUIRES_REVIEW_NO_AUTOMATIC_RESTART', error={'type': type(exc).__name__, 'message': str(exc)})
    finally:
        state['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
        state['driver_unchanged'] = sha(Path(__file__)) == state['driver_sha256']
        save()
    print(json.dumps({k: state.get(k) for k in ['status', 'error']}))
    return int(state['status'] != 'TECHNICAL24H_EVIDENCE_COMPLETE_REQUIRES_AGENT_ACCEPTANCE_REVIEW')


if __name__ == '__main__':
    raise SystemExit(main())
