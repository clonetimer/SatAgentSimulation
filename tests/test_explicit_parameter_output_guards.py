"""Literal user engineering inputs and outputs must survive model generation."""
import copy
import json
import pytest
import yaml

from sat_sim.capability_agent import (
    CapabilityAgentRequest, _example_for_prompt, _explicit_literal_requirement_issues,
    _normalize_external_draft, _validate_generated_draft, run_capability_script_generation,
)
from sat_sim.scenario_templates import instantiate_scenario_template
from sat_sim.task_models import canonicalize_task_spec


def case():
    spec = canonicalize_task_spec(instantiate_scenario_template('generic_leo_small_sat_engineering'))
    spec['simulation'].update(duration_s=20.,sample_s=1.)
    spec['simulation']['step_s']=.2
    spec['outputs']['plots']=['thermal.payload_earth_ir_heat_w','thermal.payload_albedo_heat_w']
    text = ('whole_spacecraft.unified_native.v1，仿真20秒，记录周期1秒，步长0.2秒。'
            'environment_profile=spice_wmm，epoch_utc=2026-01-01T00:00:00Z。'
            'thermal_earth_ir_w_m2=234，thermal_earth_albedo=.30。'
            '输出 thermal.payload_earth_ir_heat_w、thermal.payload_albedo_heat_w。')
    return spec,text


def test_exact_registered_assignments_and_outputs_pass_without_mutation():
    spec,text=case()
    before=copy.deepcopy(spec)
    assert not _explicit_literal_requirement_issues(spec,text)
    assert _validate_generated_draft(spec,CapabilityAgentRequest(request=text)).ok
    assert spec==before


@pytest.mark.parametrize(('key','wrong'),[
    ('environment_profile','simplified'),('epoch_utc','2026-07-01T00:00:00Z'),
    ('thermal_earth_ir_w_m2',241.),('thermal_earth_albedo',.35),
])
def test_registered_value_change_or_missing_is_refused(key,wrong):
    spec,text=case()
    spec['parameters']['values'][key]=wrong
    assert 'USER_PARAMETER_REQUIREMENT_MISMATCH' in {i.code for i in _explicit_literal_requirement_issues(spec,text)}
    del spec['parameters']['values'][key]
    assert 'USER_PARAMETER_REQUIREMENT_MISMATCH' in {i.code for i in _explicit_literal_requirement_issues(spec,text)}


def test_latest_refinement_assignment_overrides_prior_request_only():
    spec,_=case()
    spec['parameters']['values']['thermal_earth_ir_w_m2']=241.
    text='thermal_earth_ir_w_m2=234。本轮补充指令：thermal_earth_ir_w_m2=241。'
    assert not _explicit_literal_requirement_issues(spec,text)


def test_concrete_output_omission_is_refused():
    spec,text=case()
    spec['outputs']['plots']=[]
    assert len([i for i in _explicit_literal_requirement_issues(spec,text) if i.code=='USER_OUTPUT_REQUIREMENT_MISMATCH'])==2


@pytest.mark.parametrize('bad_time',[.2,None])
def test_requested_recording_period_is_not_silently_changed_to_step(bad_time):
    spec,text=case()
    if bad_time is None:
        del spec['simulation']['sample_s']
    else:
        spec['simulation']['sample_s']=bad_time
    assert not _validate_generated_draft(spec,CapabilityAgentRequest(request=text)).ok


def test_step_alias_preserves_independent_recording_period():
    cid='whole_spacecraft.unified_native.v1'
    spec=yaml.safe_load(_example_for_prompt(cid))
    spec['simulation'].update(sample_s=1.,step_s=.2)
    normalized=_normalize_external_draft(spec,CapabilityAgentRequest(request='工程仿真',allowed_capabilities=(cid,)))
    assert normalized['simulation']['sample_s']==1.
    assert normalized['simulation']['solver']['step_s']==.2


def test_nested_solver_step_is_not_replaced_by_sample_default():
    cid='whole_spacecraft.unified_native.v1'
    spec=yaml.safe_load(_example_for_prompt(cid))
    spec['simulation'].update(sample_s=1.,solver={'step_s':.2})
    normalized=_normalize_external_draft(spec,CapabilityAgentRequest(request='工程仿真',allowed_capabilities=(cid,)))
    assert normalized['simulation']['sample_s']==1.
    assert normalized['simulation']['solver']['step_s']==.2
    assert normalized['simulation'].get('step_s',.2)==.2


def test_user_literal_plot_binding_is_explicit_and_never_changes_model_parameters():
    spec,text=case()
    from sat_sim.task_models import to_runtime_task_spec
    draft=to_runtime_task_spec(spec)
    draft['outputs']['plots']=[]
    before=copy.deepcopy(draft)
    normalized=_normalize_external_draft(draft,CapabilityAgentRequest(request=text))
    assert draft==before
    assert normalized['parameters']==before['parameters']
    assert set(normalized['outputs']['plots']) >= {'thermal.payload_earth_ir_heat_w','thermal.payload_albedo_heat_w'}
    binding=normalized['metadata']['explicit_output_bindings']
    assert binding['source']=='user_literal_registry_field_names'
    assert binding['raw_model_answer_preserved'] is True
    assert binding['selection_augmented_from_user_literals'] is True
    assert binding['physical_parameters_replaced'] is False
    assert not _explicit_literal_requirement_issues(normalized,text)
    assert set(to_runtime_task_spec(canonicalize_task_spec(normalized))['outputs']['plots']) >= set(normalized['outputs']['plots'])


def test_negated_literal_output_is_not_bound_as_a_positive_selection():
    from sat_sim.capability_agent import _requested_declared_output_fields
    from sat_sim.capability_registry import get_capability
    fields=_requested_declared_output_fields(
        '不要输出 thermal.payload_albedo_heat_w，只输出 thermal.payload_earth_ir_heat_w。',
        get_capability('whole_spacecraft.unified_native.v1').data)
    assert fields=={'thermal.payload_earth_ir_heat_w'}


def test_wrong_realistic_environment_never_compiles_or_exports(tmp_path):
    spec,text=case()
    spec['parameters']['values']['environment_profile']='simplified'
    draft=tmp_path/'wrong-environment.json'
    draft.write_text(json.dumps(spec),encoding='utf-8')
    result=run_capability_script_generation(CapabilityAgentRequest(request=text,draft_spec_path=draft,
        allowed_capabilities=('whole_spacecraft.unified_native.v1',),output_dir=tmp_path/'out',max_repair_attempts=2))
    assert not result.ok
    assert result.compiled is None and result.script is None
    assert 'USER_PARAMETER_REQUIREMENT_MISMATCH' in {i.code for i in result.validation.errors}
