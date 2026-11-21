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

@pytest.mark.parametrize('phrase', ['NRLMSISE-00', 'NRLMSISE00'])
def test_named_native_atmosphere_template_exports_real_opt_in(tmp_path, phrase):
    request = CapabilityAgentRequest(
        request=f'用 whole_spacecraft.unified_native.v1 创建整星仿真，使用{phrase}大气阻力，运行20秒',
        backend='template',
        allowed_capabilities=('whole_spacecraft.unified_native.v1',),
        output_dir=tmp_path / 'agent',
        run_task=False,
    )
    result = run_capability_script_generation(request)
    assert result.ok, result.validation.to_dict()
    assert result.script is not None
    spec = canonicalize_task_spec(result.task_spec)
    values = spec['parameters']['values']
    assert values['environment_profile'] == 'spice_wmm'
    assert values['atmosphere_model'] == 'nrlmsise00'
    code = result.script.output_path.read_text(encoding='utf-8')
    assert 'nrlmsise00' in code and 'spice_wmm' in code


def test_named_native_atmosphere_draft_must_not_silently_disable_drag():
    request = 'whole_spacecraft.unified_native.v1 使用 NRLMSISE-00 大气阻力'
    spec = instantiate_scenario_template('whole_spacecraft_unified_native')
    before = copy.deepcopy(spec)
    issues = _explicit_literal_requirement_issues(spec, request)
    assert {item.path for item in issues if item.code == 'USER_PARAMETER_REQUIREMENT_MISMATCH'} == {
        '$.parameters.atmosphere_model',
        '$.parameters.environment_profile',
    }
    assert spec == before
    spec['parameters']['values'].update(
        atmosphere_model='nrlmsise00', environment_profile='spice_wmm')
    assert not _explicit_literal_requirement_issues(spec, request)


@pytest.mark.parametrize('negative_request', [
    '不要使用 NRLMSISE-00', '不使用 NRLMSISE-00',
    '不启用 NRLMSISE-00', '禁用 NRLMSISE-00',
    '停用 NRLMSISE-00', '关闭 NRLMSISE-00',
    'disable NRLMSISE-00', 'turn off NRLMSISE-00',
])
def test_negated_native_atmosphere_never_enables_drag(tmp_path, negative_request):
    from sat_sim.capability_agent import _requests_native_nrlmsise, _forbids_native_nrlmsise
    request = f'whole_spacecraft.unified_native.v1 {negative_request}，运行20秒'
    assert not _requests_native_nrlmsise(request)
    assert _forbids_native_nrlmsise(request)
    spec = instantiate_scenario_template('whole_spacecraft_unified_native')
    assert not _explicit_literal_requirement_issues(spec, request)

    # Real deterministic script generation must preserve the default off.
    result = run_capability_script_generation(CapabilityAgentRequest(
        request=request, backend='template',
        allowed_capabilities=('whole_spacecraft.unified_native.v1',),
        output_dir=tmp_path / 'no_drag', run_task=False))
    assert result.ok, result.validation.to_dict()
    values = canonicalize_task_spec(result.task_spec)['parameters']['values']
    assert values.get('atmosphere_model', 'none') == 'none'
    assert values.get('environment_profile', 'simplified') == 'simplified'

    # Counterexample: external model draft cannot contradict explicit off.
    spec['parameters']['values'].update(
        atmosphere_model='nrlmsise00', environment_profile='spice_wmm')
    issues = _explicit_literal_requirement_issues(spec, request)
    assert any(
        issue.code == 'USER_PARAMETER_REQUIREMENT_MISMATCH'
        and issue.path == '$.parameters.atmosphere_model'
        for issue in issues)


def test_flight_grade_disclaimer_is_not_a_model_disable_request():
    from sat_sim.capability_agent import _requests_native_nrlmsise, _forbids_native_nrlmsise
    request = 'whole_spacecraft.unified_native.v1 使用 NRLMSISE-00，但不要声称飞行级精度'
    assert _requests_native_nrlmsise(request)
    assert not _forbids_native_nrlmsise(request)


@pytest.mark.parametrize('phrase,field,value', [
    ('IGRF-14 地磁模型', 'magnetic_field_model', 'igrf14'),
    ('GGM03S degree 4 重力', 'earth_gravity_model', 'ggm03s_degree4'),
    ('J4 gravity', 'earth_gravity_model', 'ggm03s_degree4'),
    ('GGM03S degree 3', 'earth_gravity_model', 'ggm03s_degree3'),
    ('GGM03S degree 2', 'earth_gravity_model', 'ggm03s_degree2'),
])
def test_named_field_and_gravity_template_exports_real_native_opt_in(tmp_path, phrase, field, value):
    result = run_capability_script_generation(CapabilityAgentRequest(
        request=f'whole_spacecraft.unified_native.v1 使用 {phrase}，仿真20秒',
        backend='template',
        allowed_capabilities=('whole_spacecraft.unified_native.v1',),
        output_dir=tmp_path / 'script', run_task=False))
    assert result.ok, result.validation.to_dict()
    values = canonicalize_task_spec(result.task_spec)['parameters']['values']
    assert values[field] == value
    assert values['environment_profile'] == 'spice_wmm'
    assert value in result.script.output_path.read_text(encoding='utf-8')
    assert values.get('atmosphere_model', 'none') == 'none'
    assert values.get('srp_model', 'disabled') == 'disabled'


@pytest.mark.parametrize('phrase,field,value', [
    ('IGRF14', 'magnetic_field_model', 'igrf14'),
    ('GGM03S degree 4', 'earth_gravity_model', 'ggm03s_degree4'),
])
def test_named_native_draft_must_not_silently_fall_back_to_default(phrase, field, value):
    draft = instantiate_scenario_template('whole_spacecraft_unified_native')
    original = copy.deepcopy(draft)
    instruction = f'whole_spacecraft.unified_native.v1 使用 {phrase}'
    issues = _explicit_literal_requirement_issues(draft, instruction)
    assert '$.parameters.' + field in {issue.path for issue in issues}
    assert draft == original
    draft['parameters']['values'][field] = value
    draft['parameters']['values']['environment_profile'] = 'spice_wmm'
    assert not _explicit_literal_requirement_issues(draft, instruction)


@pytest.mark.parametrize('instruction,field,value', [
    ('不要启用 IGRF-14', 'magnetic_field_model', 'igrf14'),
    ('不使用 GGM03S degree 4', 'earth_gravity_model', 'ggm03s_degree4'),
    ('禁用 Cannonball SRP', 'srp_model', 'cannonball'),
])
def test_named_native_model_disable_is_never_positive_opt_in(tmp_path, instruction, field, value):
    request = 'whole_spacecraft.unified_native.v1 ' + instruction
    result = run_capability_script_generation(CapabilityAgentRequest(
        request=request, backend='template',
        allowed_capabilities=('whole_spacecraft.unified_native.v1',),
        output_dir=tmp_path / 'disabled', run_task=False))
    assert result.ok, result.validation.to_dict()
    values = canonicalize_task_spec(result.task_spec)['parameters']['values']
    assert values.get(field) != value
    draft = instantiate_scenario_template('whole_spacecraft_unified_native')
    draft['parameters']['values'][field] = value
    assert '$.parameters.' + field in {
        issue.path for issue in _explicit_literal_requirement_issues(draft, request)}


def test_named_cannonball_srp_requires_area_and_cr_without_guessing(tmp_path):
    instruction = 'whole_spacecraft.unified_native.v1 使用 Cannonball SRP，仿真20秒'
    draft = instantiate_scenario_template('whole_spacecraft_unified_native')
    errors = _explicit_literal_requirement_issues(draft, instruction)
    assert {i.path for i in errors if i.code == 'USER_PHYSICS_INPUT_REQUIRED'} == {
        '$.parameters.srp_reference_area_m2', '$.parameters.srp_coefficient_reflection'}
    # Even an LLM that invented plausible area/Cr must be rejected when
    # the user provided neither number.
    draft['parameters']['values'].update(
        srp_model='cannonball', environment_profile='spice_wmm',
        srp_reference_area_m2=20, srp_coefficient_reflection=1.5)
    guessed = _explicit_literal_requirement_issues(draft, instruction)
    assert {i.path for i in guessed if i.code == 'USER_PHYSICS_INPUT_REQUIRED'} == {
        '$.parameters.srp_reference_area_m2', '$.parameters.srp_coefficient_reflection'}
    result = run_capability_script_generation(CapabilityAgentRequest(
        request=instruction + '，srp_reference_area_m2=20，srp_coefficient_reflection=1.5',
        backend='template',
        allowed_capabilities=('whole_spacecraft.unified_native.v1',),
        output_dir=tmp_path / 'srp', run_task=False))
    assert result.ok, result.validation.to_dict()
    values = canonicalize_task_spec(result.task_spec)['parameters']['values']
    assert values['srp_model'] == 'cannonball'
    assert values['environment_profile'] == 'spice_wmm'
    assert values['srp_reference_area_m2'] == 20
    assert values['srp_coefficient_reflection'] == 1.5
    assert 'cannonball' in result.script.output_path.read_text(encoding='utf-8')


def test_unspecified_and_conflicting_ggm03s_degrees_fail_closed():
    draft = instantiate_scenario_template('whole_spacecraft_unified_native')
    prefix = 'whole_spacecraft.unified_native.v1 使用 '
    assert 'USER_PHYSICS_INPUT_REQUIRED' in {
        x.code for x in _explicit_literal_requirement_issues(draft, prefix + 'GGM03S')}
    assert 'USER_PARAMETER_REQUIREMENT_INVALID' in {
        x.code for x in _explicit_literal_requirement_issues(
            draft, prefix + 'GGM03S degree 3 和 GGM03S degree 4')}
