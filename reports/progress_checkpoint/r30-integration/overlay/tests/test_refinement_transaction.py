"""Real facade/merge/export transactions with declared drafts, without inference."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest
import yaml

from sat_sim.agent_facade import _request_explicitly_sets
from sat_sim.form_schema import capability_form_schema
from sat_sim.task_models import canonicalize_task_spec
from sat_sim.unified_agent import UnifiedAgentRequest, normalize_form_task_spec, run_unified_agent


CID = 'whole_spacecraft.unified_native.v1'


def base_spec():
    spec=normalize_form_task_spec(deepcopy(capability_form_schema(CID)['default_form']))
    spec['simulation'].update(duration_s=20.,step_s=1.,sample_s=10.,solver={'step_s':1.})
    spec['outputs']['plots']=['eps.battery_soc']
    return canonicalize_task_spec(spec)


def inject_declared_draft(monkeypatch,tmp_path,spec):
    import sat_sim.agent_facade as facade_module
    import sat_sim.unified_agent as unified_module
    real=facade_module.run_agent
    path=tmp_path/'declared-draft.json'
    path.write_text(json.dumps(spec),encoding='utf-8')
    captured=[]
    def run(request):
        result=real(replace(request,draft_spec_path=path,backend='template',max_repair_attempts=0))
        captured.append(result)
        return result
    monkeypatch.setattr(unified_module,'run_agent',run)
    return captured,path


def forbid_export(*args,**kwargs):
    pytest.fail('An intermediate draft must never be exported')


def test_only_final_merged_spec_is_exported(monkeypatch,tmp_path):
    import sat_sim.agent_facade as facade_module
    import sat_sim.unified_agent as unified_module
    base=base_spec();draft=deepcopy(base)
    draft['simulation']['duration_s']=30.
    captured,path=inject_declared_draft(monkeypatch,tmp_path,draft)
    before=path.read_bytes()
    monkeypatch.setattr(facade_module,'export_runner_script',forbid_export)
    real_export=unified_module.export_runner_script
    exports=[]
    def export(spec_path,*args,**kwargs):
        final=yaml.safe_load(Path(spec_path).read_text(encoding='utf-8'))
        assert final['simulation']['duration_s']==30.
        assert final['simulation']['sample_s']==base['simulation']['sample_s']
        exports.append(final)
        return real_export(spec_path,*args,**kwargs)
    monkeypatch.setattr(unified_module,'export_runner_script',export)
    result=run_unified_agent(UnifiedAgentRequest(request_text='把仿真时长改为30秒，其他配置不变。',
        base_task_spec=base,backend='template',output_dir=tmp_path/'out'))
    assert result.ok,result.validation.to_dict()
    assert len(exports)==1 and captured[0].request.compile_if_valid is False
    assert captured[0].compiled is None and captured[0].script is None
    assert result.compiled is not None and result.script is not None
    assert path.read_bytes()==before


def test_no_compile_request_is_honored_through_natural_language_path(monkeypatch,tmp_path):
    import sat_sim.agent_facade as facade_module
    import sat_sim.unified_agent as unified_module
    base=base_spec();inject_declared_draft(monkeypatch,tmp_path,base)
    monkeypatch.setattr(facade_module,'export_runner_script',forbid_export)
    monkeypatch.setattr(unified_module,'export_runner_script',forbid_export)
    result=run_unified_agent(UnifiedAgentRequest(request_text='保留当前全部配置。',base_task_spec=base,
        backend='template',compile_if_valid=False,output_dir=tmp_path/'out'))
    assert result.ok,result.validation.to_dict()
    assert result.compiled is None and result.script is None
    assert 'generated_script' not in result.files
    assert not (tmp_path/'out/generated_simulation.py').exists()


def test_merge_incompatible_with_preserved_sampling_returns_validation_without_export(monkeypatch,tmp_path):
    import sat_sim.agent_facade as facade_module
    import sat_sim.unified_agent as unified_module
    base=base_spec();draft=deepcopy(base)
    draft['simulation'].update(duration_s=5.,sample_s=1.,step_s=.5,solver={'step_s':.5})
    inject_declared_draft(monkeypatch,tmp_path,canonicalize_task_spec(draft))
    monkeypatch.setattr(facade_module,'export_runner_script',forbid_export)
    monkeypatch.setattr(unified_module,'export_runner_script',forbid_export)
    result=run_unified_agent(UnifiedAgentRequest(request_text='把仿真时长改为5秒，其他配置不变。',
        base_task_spec=base,backend='template',output_dir=tmp_path/'out'))
    assert not result.ok
    assert 'NATURAL_LANGUAGE_REFINEMENT_INVALID' in result.reason_codes
    assert 'NATURAL_LANGUAGE_REFINEMENT_APPLIED' not in result.reason_codes
    assert result.task_spec=={} and result.compiled is None and result.script is None
    assert result.validation.errors and result.planning is None
    assert 'generated_script' not in result.files and 'compiled_task' not in result.files
    assert Path(result.files['refinement_failure']).is_file()
    assert Path(result.files['refinement_candidate_task_spec_json']).is_file()
    assert not (tmp_path/'out/generated_simulation.py').exists()


def test_machine_parameter_names_do_not_become_new_output_requests(monkeypatch,tmp_path):
    base=base_spec()
    base['metadata']['historic_request']='输出姿态角和陀螺曲线。'
    draft=deepcopy(base)
    draft['simulation']['duration_s']=30.
    captured,_=inject_declared_draft(monkeypatch,tmp_path,draft)
    instruction='保留所有输出，只把仿真时长改为30秒。'
    result=run_unified_agent(UnifiedAgentRequest(request_text=instruction,base_task_spec=base,
        backend='template',output_dir=tmp_path/'out'))
    assert result.ok,result.validation.to_dict()
    assert captured[0].request.user_instruction==instruction
    assert captured[0].task_spec['outputs']==base['outputs']
    assert result.task_spec['outputs']==base['outputs']


@pytest.mark.parametrize('text',[
    '把电池容量损失从11%改为37%。',
    'Set battery capacity loss to 36%.',
    '把电池容量损失设为0.29。',
])
def test_capacity_loss_is_not_provenance_of_nominal_wh(text):
    assert not _request_explicitly_sets('parameters.values.battery_capacity_wh',text)


@pytest.mark.parametrize('text',[
    '把电池容量改为208 Wh。',
    'Set battery_capacity_wh=192.',
])
def test_energy_capacity_literal_keeps_explicit_provenance(text):
    assert _request_explicitly_sets('parameters.values.battery_capacity_wh',text)
