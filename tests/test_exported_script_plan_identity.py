"""Local engineering regression: exported runner plans must reach actual execution.

Subprocess cases use real registered/native adapters. In-process fault cases below
exercise only plan/control flow and must not be described as container security
audits or model/physics accuracy evidence.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from dataclasses import replace

import pytest

from sat_sim.assembly_graph import assembly_contract, assembly_runtime_metadata
from sat_sim.execution_planner import plan_task_spec
from sat_sim.form_schema import capability_form_schema
from sat_sim.script_exporter import export_runner_script, export_assembly_python_runner
from sat_sim.task_models import canonicalize_task_spec
from sat_sim.unified_agent import normalize_form_task_spec

ROOT=Path(__file__).resolve().parents[1]
KINDS=('python','capability-python','assembly-python')

def _export(tmp_path,kind,capability=None):
    capability=capability or ('whole_spacecraft.power_thermal_orbit_coupled.v1' if kind=='assembly-python' else 'component.thermal_node.v1')
    form=copy.deepcopy(capability_form_schema(capability)['default_form'])
    form['simulation'].update(duration_s=2.,sample_s=.5,step_s=.1)
    form['simulation']['solver']={'step_s':.1}
    if kind=='assembly-python':
        graph=assembly_contract(capability)['default_graph']
        form.setdefault('metadata',{})['visual_assembly']=assembly_runtime_metadata(graph)
    else:graph=None
    spec=normalize_form_task_spec(form)
    spec.setdefault('outputs',{})['output_root']=str(tmp_path/'dataset')
    source=tmp_path/'task.json';source.write_text(json.dumps(spec),encoding='utf-8')
    exported=(export_assembly_python_runner(source,tmp_path/'run.py',output_root=tmp_path/'dataset',assembly_graph=graph)
              if kind=='assembly-python' else export_runner_script(source,tmp_path/'run.py',kind=kind,output_root=tmp_path/'dataset'))
    definition=importlib.util.spec_from_file_location('exported_plan_runner',exported.output_path)
    module=importlib.util.module_from_spec(definition);definition.loader.exec_module(module)
    return spec,source,exported.output_path,module

def _hashes(value):
    if isinstance(value,dict):
        for key,item in value.items():
            if key=='execution_plan_sha256':yield item
            yield from _hashes(item)
    elif isinstance(value,list):
        for item in value:yield from _hashes(item)

def _run(path,args,tmp_path):
    env={**os.environ,'PYTHONPATH':str(ROOT/'src')}
    result=subprocess.run([sys.executable,str(path),*args],cwd=ROOT,env=env,capture_output=True,timeout=90)
    (tmp_path/'stdout.log').write_bytes(result.stdout);(tmp_path/'stderr.log').write_bytes(result.stderr)
    assert result.returncode==0,result.stderr.decode('utf-8',errors='replace') or result.stdout.decode('utf-8',errors='replace')
    return json.loads(result.stdout)

@pytest.mark.parametrize('kind',KINDS)
@pytest.mark.parametrize('edited',[False,True])
def test_exported_subprocess_records_actual_runtime_plan(tmp_path,kind,edited):
    spec,source,path,module=_export(tmp_path,kind)
    args=[]
    if kind=='python':
        if edited:
            spec['simulation']['duration_s']=3.
            source.write_text(json.dumps(spec),encoding='utf-8')
        effective=module.build_exported_task_spec(spec,output_root=module.OUTPUT_ROOT,field_sha256=module.EXPORTED_FIELD_SHA256)
        if edited:
            assert effective['provenance']['fields']['simulation.duration_s']['source']=='user_explicit'
    else:
        if edited:args=['--duration-s','3','--set','simulation.seed=42']
        namespace=module.argparse.Namespace(duration_s=3. if edited else None,sample_s=None,
            output_root=None,set=['simulation.seed=42'] if edited else [],show_task_spec=False)
        effective=module.build_task_spec(namespace)
    expected=plan_task_spec(canonicalize_task_spec(effective))
    assert expected.ok and expected.execution_plan is not None
    payload=_run(path,args,tmp_path)
    expected_hash=expected.execution_plan.plan_sha256
    assert payload.get('execution_plan_sha256')==expected_hash
    compiled=json.loads((tmp_path/'dataset/compiled_task.json').read_text(encoding='utf-8'))
    assert compiled['metadata']['v24_planning']['execution_plan_sha256']==expected_hash
    manifest=json.loads((tmp_path/'dataset/manifest.json').read_text(encoding='utf-8'))
    recorded=list(_hashes(manifest.get('execution_traceability',{})))
    assert recorded and set(recorded)=={expected_hash}
    if edited:
        initial=module.TASK_SPEC if kind!='python' else {**spec,'simulation':{**spec['simulation'],'duration_s':2.}}
        initial_plan=plan_task_spec(canonicalize_task_spec(initial))
        assert initial_plan.ok and initial_plan.execution_plan.plan_sha256!=expected_hash

@pytest.mark.parametrize('kind',KINDS)
@pytest.mark.parametrize('bad_plan',['rejected','missing','corrupt'])
def test_bad_runtime_plan_never_calls_execution(tmp_path,monkeypatch,kind,bad_plan):
    _,_,_,module=_export(tmp_path,kind)
    original=module.plan_task_spec
    def altered(spec):
        planning=original(spec)
        assert planning.ok
        if bad_plan=='rejected':return replace(planning,ok=False)
        if bad_plan=='missing':return replace(planning,execution_plan=None)
        return replace(planning,execution_plan=planning.execution_plan.model_copy(update={'plan_sha256':'0'*64}))
    monkeypatch.setattr(module,'plan_task_spec',altered)
    called=[]
    def forbidden(*args,**kwargs):
        called.append(True);raise AssertionError('invalid plan reached execution')
    monkeypatch.setattr(module,'execute_compiled_task',forbidden)
    monkeypatch.setattr(sys,'argv',[str(tmp_path/'run.py')])
    assert module.main()==1
    assert not called
    assert not (tmp_path/'dataset/manifest.json').exists()

@pytest.mark.parametrize('kind',KINDS)
def test_real_planner_rejection_stops_new_process_before_dataset(tmp_path,kind):
    spec,source,path,module=_export(tmp_path,kind)
    request='component level simulation' if kind=='assembly-python' else 'whole spacecraft simulation'
    spec.setdefault('metadata',{}).setdefault('agent',{})['request_text']=request
    if kind=='python':
        source.write_text(json.dumps(spec),encoding='utf-8');args=[]
    else:
        args=['--set','metadata.agent.request_text='+json.dumps(request)]
    expected=plan_task_spec(canonicalize_task_spec(spec))
    assert not expected.ok
    env={**os.environ,'PYTHONPATH':str(ROOT/'src')}
    result=subprocess.run([sys.executable,str(path),*args],cwd=ROOT,env=env,capture_output=True,timeout=60)
    (tmp_path/'stdout.log').write_bytes(result.stdout);(tmp_path/'stderr.log').write_bytes(result.stderr)
    assert result.returncode==1,result.stderr.decode('utf-8',errors='replace')
    payload=json.loads(result.stdout)
    assert payload['ok'] is False and payload['planning']['ok'] is False
    assert 'REQUEST_LEVEL_CONFLICT' in {issue['code'] for issue in payload['planning']['validation']['issues']}
    assert not (tmp_path/'dataset').exists()

@pytest.mark.parametrize('kind',['python','capability-python'])
def test_short_native_export_has_validated_plan_identity(tmp_path,kind):
    spec,source,path,module=_export(tmp_path,kind,'whole_spacecraft.unified_native.v1')
    effective=spec if kind=='python' else module.TASK_SPEC
    expected=plan_task_spec(canonicalize_task_spec(effective))
    assert expected.ok
    payload=_run(path,[],tmp_path)
    assert payload['execution_plan_sha256']==expected.execution_plan.plan_sha256
    compiled=json.loads((tmp_path/'dataset/compiled_task.json').read_text(encoding='utf-8'))
    assert compiled['metadata']['v24_planning']['execution_plan_sha256']==payload['execution_plan_sha256']
    manifest=json.loads((tmp_path/'dataset/manifest.json').read_text(encoding='utf-8'))
    hashes=list(_hashes(manifest.get('execution_traceability',{})))
    assert hashes and set(hashes)=={payload['execution_plan_sha256']}
