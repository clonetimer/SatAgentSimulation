"""Build and serve an isolated compact UI preview without changing production."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import runpy
import shutil
import socket
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
PROTOTYPE=ROOT/'prototypes/compact-workbench'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--native-runtime',choices=['supervised','in-process'],default='supervised',
                        help='Isolated preview only: continuous sessions use supervised; bounded sessions use in-process.')
    parser.add_argument('--dependency-dir',type=Path,
                        help='Optional isolated uv --target packages, without modifying the main environment.')
    args=parser.parse_args()
    if not 0<=args.port<=65535:
        parser.error('port must be 0..65535')
    local=ROOT/'reports/local_requirements_completion'
    local.mkdir(parents=True,exist_ok=True)
    dependency_dir=args.dependency_dir
    default_dependencies=local/'compact-ui-websocket-deps'
    if dependency_dir is None and default_dependencies.is_dir():
        dependency_dir=default_dependencies
    if dependency_dir is not None:
        dependency_dir=dependency_dir.resolve()
        if not (dependency_dir/'websockets/__init__.py').is_file():
            parser.error('--dependency-dir must contain the installed websockets package')
    if not args.prepare_only and dependency_dir is None and importlib.util.find_spec('websockets') is None:
        parser.error('WebSocket dependency missing. Install prototypes/compact-workbench/requirements-preview.txt with uv --target reports/local_requirements_completion/compact-ui-websocket-deps, or pass --dependency-dir.')
    output=Path(tempfile.mkdtemp(prefix='compact-ui-preview-',dir=local)).resolve()
    if not output.is_relative_to(local.resolve()):
        raise ValueError('PREVIEW_OUTSIDE_WORKSPACE')
    module=runpy.run_path(str(ROOT/'scripts/run_native_endurance.py'))
    manifest=module['application_source_manifest']()
    fingerprint=module['runtime_source_fingerprint']
    for row in manifest['files']:
        target=output/row['path']
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/row['path'],target)
        if hashlib.sha256(target.read_bytes()).hexdigest()!=row['sha256']:
            raise ValueError('PREVIEW_SOURCE_COPY_MISMATCH')
    for name in ['configs','examples']:
        shutil.copytree(ROOT/name,output/name)
    overlay=json.loads((PROTOTYPE/'manifest.json').read_text(encoding='utf-8'))
    for row in overlay['files']:
        source=(PROTOTYPE/row['path']).resolve()
        target=(output/row['target']).resolve()
        if not source.is_relative_to(PROTOTYPE.resolve()) or not target.is_relative_to(output/'src/sat_sim/web'):
            raise ValueError('PREVIEW_OVERLAY_PATH_INVALID')
        if hashlib.sha256(source.read_bytes()).hexdigest()!=row['sha256']:
            raise ValueError('PREVIEW_OVERLAY_HASH_MISMATCH')
        shutil.copyfile(source,target)
    port=args.port
    if not port:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            port=sock.getsockname()[1]
    state={'scope':'Isolated preview; not production qualification. Own new run/artifact directories.',
           'workspace':str(output),'url':f'http://127.0.0.1:{port}',
           'base_application':fingerprint(manifest),
           'preview_application':fingerprint(module['application_source_manifest'](output)),
           'prototype_base_application':overlay['base_application'],'native_runtime':args.native_runtime,
           'dependency_dir':str(dependency_dir) if dependency_dir else None}
    if dependency_dir:
        state['dependency_files']=[{'path':str(p.relative_to(dependency_dir)),
            'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(dependency_dir.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc']
    state_path=output/'preview-state.json'
    state_path.write_text(json.dumps(state,indent=2),encoding='utf-8')
    print(json.dumps({key:value for key,value in state.items() if key!='dependency_files'}),flush=True)
    if args.prepare_only:
        return 0
    pythonpath=os.pathsep.join([str(output/'src')]+([str(dependency_dir)] if dependency_dir else []))
    env={**os.environ,'PYTHONPATH':pythonpath,'SAT_SIM_ASSET_ROOT':str(ROOT/'third_party'),
         'SAT_SIM_INTERACTIVE_ENABLED':'1','SAT_SIM_SUPERVISED_NATIVE_RUNTIME':'1' if args.native_runtime=='supervised' else '0',
         'SAT_SIM_AUTH_MODE':'disabled','SAT_SIM_EMBEDDED_WORKER':'1',
         'SAT_SIM_RUNS_ROOT':str(output/'preview-runs'),'SAT_SIM_API_ARTIFACTS_ROOT':str(output/'preview-api')}
    for key in ['SAT_SIM_QUEUE_DATABASE','SAT_SIM_TASK_CENTER_DATABASE','SAT_SIM_EXPERIMENT_DATABASE',
                'SAT_SIM_AUDIT_LOG','SAT_SIM_WORKBENCH_LOG','SAT_SIM_MODEL_PROVIDERS_FILE']:
        env.pop(key,None)
    process=subprocess.Popen([sys.executable,'-m','uvicorn','sat_sim.api:create_app','--factory',
        '--host','127.0.0.1','--port',str(port),'--ws','websockets','--no-access-log'],cwd=output,env=env,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    state['server_pid']=process.pid
    state_path.write_text(json.dumps(state,indent=2),encoding='utf-8')
    try:
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=15)
        return 0


if __name__=='__main__':
    raise SystemExit(main())
