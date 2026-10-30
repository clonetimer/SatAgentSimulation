"""Public canonical identity and provenance must survive script generation.

The recorded fixture is copied actual AI output and replayed offline; these
tests make no new model call and do not estimate arbitrary-prompt accuracy.
"""
import argparse
import copy
import importlib.util
import json
from pathlib import Path

import pytest

from sat_sim.assembly_graph import assembly_contract,assembly_runtime_metadata
from sat_sim.execution_planner import plan_task_spec
from sat_sim.form_schema import capability_form_schema
from sat_sim.script_exporter import export_runner_script,export_assembly_python_runner
from sat_sim.task_models import canonicalize_task_spec,to_runtime_task_spec
from sat_sim.unified_agent import normalize_form_task_spec

FIXTURE=Path(__file__).with_name('fixtures')/'export_roundtrip/create17_task_spec.json'

def _module(tmp_path,spec,kind='capability-python',graph=None):
    source=tmp_path/'source.json';source.write_text(json.dumps(spec),encoding='utf-8')
    if kind=='assembly-python':exported=export_assembly_python_runner(source,tmp_path/'run.py',assembly_graph=graph)
    else:exported=export_runner_script(source,tmp_path/'run.py',kind=kind)
    definition=importlib.util.spec_from_file_location('canonical_export_runner',exported.output_path)
    module=importlib.util.module_from_spec(definition);definition.loader.exec_module(module)
    return module

def _args(*items,**values):
    return argparse.Namespace(duration_s=values.get('duration_s'),sample_s=values.get('sample_s'),
        output_root=values.get('output_root'),set=list(items),show_task_spec=False)

def test_recorded_actual_ai_canonical_export_is_identity_without_edits(tmp_path):
    spec=json.loads(FIXTURE.read_text(encoding='utf-8'));expected=canonicalize_task_spec(spec)
    before=plan_task_spec(expected);assert before.ok
    assert before.execution_plan.plan_sha256=='d7499066174fc195d1ceb90e40df5da8dc613009d80bdfcfac16ce92592220c7'
    module=_module(tmp_path,spec)
    actual=module.build_task_spec(_args())
    assert actual==expected
    after=plan_task_spec(actual)
    assert after.ok and after.execution_plan.plan_sha256==before.execution_plan.plan_sha256
    assert after.resolved_spec.resolved_spec_sha256==before.resolved_spec.resolved_spec_sha256

@pytest.mark.parametrize('kind',['capability-python','assembly-python'])
def test_canonical_exports_keep_public_fields_and_history(tmp_path,kind):
    capability='whole_spacecraft.power_thermal_orbit_coupled.v1' if kind=='assembly-python' else 'component.thermal_node.v1'
    form=copy.deepcopy(capability_form_schema(capability)['default_form'])
    graph=assembly_contract(capability)['default_graph'] if kind=='assembly-python' else None
    if graph:form.setdefault('metadata',{})['visual_assembly']=assembly_runtime_metadata(graph)
    spec=normalize_form_task_spec(form)
    spec.setdefault('metadata',{}).setdefault('taskspec_src_alignment',{})['repair_count']=3
    module=_module(tmp_path,spec,kind,graph)
    actual=module.build_task_spec(_args())
    assert actual==canonicalize_task_spec(spec)
    assert module.TASK_SPEC['schema_version']=='1.0.0'

@pytest.mark.parametrize('mode',['runtime-alias','canonical-path','editable-task-spec'])
def test_value_edits_replace_only_affected_provenance(tmp_path,mode):
    spec=json.loads(FIXTURE.read_text(encoding='utf-8'));module=_module(tmp_path,spec)
    if mode=='editable-task-spec':
        if module.TASK_SPEC['schema_version']=='1.0.0':module.TASK_SPEC['parameters']['values']['spacecraft_mass_kg']=137.
        else:module.TASK_SPEC['parameters']['spacecraft_mass_kg']=137.
        args=_args()
    else:args=_args(('parameters.values.' if mode=='canonical-path' else 'parameters.')+'spacecraft_mass_kg=137')
    actual=canonicalize_task_spec(module.build_task_spec(args))
    assert actual['parameters']['values']['spacecraft_mass_kg']==137.
    field='parameters.values.spacecraft_mass_kg';updated=actual['provenance']['fields'][field]
    assert updated['source']=='user_explicit'
    assert updated['evidence']!=spec['provenance']['fields'][field]['evidence']
    assert 'script' in updated['evidence'].lower()
    for key,value in spec['provenance']['fields'].items():
        if key!=field:assert actual['provenance']['fields'][key]==value
    assert plan_task_spec(actual).execution_plan.plan_sha256!=plan_task_spec(spec).execution_plan.plan_sha256

def test_legacy_input_and_task_metadata_overrides_remain_supported(tmp_path):
    spec=json.loads(FIXTURE.read_text(encoding='utf-8'));legacy=to_runtime_task_spec(spec)
    module=_module(tmp_path,legacy)
    actual=canonicalize_task_spec(module.build_task_spec(_args('task_id=r26_edited_task','metadata.user_note="local edit"','parameters.spacecraft_mass_kg=137')))
    assert actual['task']['id']=='r26_edited_task'
    assert actual['metadata']['user_note']=='local edit'
    assert actual['parameters']['values']['spacecraft_mass_kg']==137.
    for path in ('task.id','metadata.user_note','parameters.values.spacecraft_mass_kg'):
        assert actual['provenance']['fields'][path]['source']=='user_explicit'

def test_legacy_metadata_task_name_alias_updates_identity_and_provenance(tmp_path):
    spec=json.loads(FIXTURE.read_text(encoding='utf-8'));module=_module(tmp_path,spec)
    actual=module.build_task_spec(_args('metadata.task_name="edited display name"'))
    assert actual['task']['name']=='edited display name'
    assert actual['provenance']['fields']['task.name']['source']=='user_explicit'
    assert actual['provenance']['fields']['parameters.values.spacecraft_mass_kg']==spec['provenance']['fields']['parameters.values.spacecraft_mass_kg']

def test_legacy_fault_list_override_remains_executable_data(tmp_path):
    spec=json.loads(FIXTURE.read_text(encoding='utf-8'))
    spec['events']['faults'][0]['delivery']='modifier'
    module=_module(tmp_path,spec)
    legacy=to_runtime_task_spec(spec);faults=copy.deepcopy(legacy['modifiers']['faults'])
    faults[0]['severity']=.23
    actual=module.build_task_spec(_args('modifiers.faults='+json.dumps(faults)))
    assert actual['events']['faults'][0]['magnitude']==.23
    assert actual['provenance']['fields']['events.faults']['source']=='user_explicit'
    assert actual['provenance']['fields']['parameters.values.spacecraft_mass_kg']==spec['provenance']['fields']['parameters.values.spacecraft_mass_kg']
    assert plan_task_spec(actual).ok
