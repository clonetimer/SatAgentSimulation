"""Invalid model magnitudes must fail rather than become a different scenario."""
import copy
import hashlib
import json
import pytest
import yaml
from sat_sim.capability_agent import (CapabilityAgentRequest, _coerce_common_llm_aliases,
                                      _example_for_prompt, run_capability_script_generation)

CID = 'whole_spacecraft.unified_native.v1'


def spec(value):
    draft = yaml.safe_load(_example_for_prompt(CID))
    draft['target']['mode'] = 'fault'
    draft['faults'] = [{'fault_id':'f1','target':'whole_spacecraft','fault_type':'eps_battery_capacity_loss',
                        'onset_time_s':7.,'duration_s':-1.,'magnitude':value}]
    return draft


@pytest.mark.parametrize('value', [-.2, 17., 101., 'bad'])
def test_backend_normalization_preserves_explicit_invalid_values(value):
    draft = spec(value)
    original = copy.deepcopy(draft)
    result = _coerce_common_llm_aliases(draft, CapabilityAgentRequest(request='Create a fault simulation with ' + CID))
    assert result['faults'][0]['magnitude'] == value
    assert draft == original


@pytest.mark.parametrize('value', [-.2, 17., 101., 'bad'])
def test_real_generation_path_rejects_invalid_physical_magnitude(value,tmp_path):
    source = tmp_path/'model-draft.json'
    source.write_text(json.dumps(spec(value)),encoding='utf-8')
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    result = run_capability_script_generation(CapabilityAgentRequest(
        request='Create a fault simulation with ' + CID, draft_spec_path=source,
        output_dir=tmp_path/'out', max_repair_attempts=0, allowed_capabilities=(CID,)))
    assert not result.validation.ok, result.validation.to_dict()
    assert result.script is None
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_valid_fraction_is_preserved_in_real_generation_path(tmp_path):
    source = tmp_path/'valid-model-draft.json'
    source.write_text(json.dumps(spec(.23)),encoding='utf-8')
    result = run_capability_script_generation(CapabilityAgentRequest(
        request='Create a fault simulation with ' + CID, draft_spec_path=source,
        output_dir=tmp_path/'out', max_repair_attempts=0, allowed_capabilities=(CID,)))
    assert result.validation.ok, result.validation.to_dict()
    assert result.task_spec['faults'][0]['magnitude'] == .23


def test_execution_plan_error_blocks_compatibility_script_export(tmp_path):
    draft = spec(.23)
    draft['faults'][0]['target'] = 'eps'
    source = tmp_path/'wrong-target-model-draft.json'
    source.write_text(json.dumps(draft),encoding='utf-8')
    result = run_capability_script_generation(CapabilityAgentRequest(
        request='Create a fault simulation with ' + CID, draft_spec_path=source,
        output_dir=tmp_path/'out', max_repair_attempts=0, allowed_capabilities=(CID,)))
    assert any(issue.code == 'EFFECT_TARGET_OWNER_MISMATCH' for issue in result.validation.errors)
    assert result.script is None and not (tmp_path/'out/generated_simulation.py').exists()


def test_product_facade_never_exposes_a_preplan_compatibility_script(tmp_path):
    from sat_sim.agent_facade import AgentFacadeRequest, run_agent
    draft = spec(.23)
    draft['faults'][0]['target'] = 'eps'
    source = tmp_path/'wrong-target-facade-draft.json'
    source.write_text(json.dumps(draft),encoding='utf-8')
    result = run_agent(AgentFacadeRequest(request='Create a fault simulation with ' + CID,
        draft_spec_path=source, backend='template', output_dir=tmp_path/'out', max_repair_attempts=0))
    assert not result.ok and 'EFFECT_TARGET_OWNER_MISMATCH' in result.reason_codes
    assert result.script is None and result.compiled is None
    assert 'generated_script' not in result.files
    assert not (tmp_path/'out/generated_simulation.py').exists()


def test_explicit_zero_onset_is_not_replaced_with_a_user_request_value():
    draft = spec(.23)
    draft['faults'][0]['onset_time_s'] = 0.
    result = _coerce_common_llm_aliases(draft, CapabilityAgentRequest(request='Create a fault simulation; fault at 7 seconds.'))
    assert result['faults'][0]['onset_time_s'] == 0.


def test_user_event_time_conflict_is_rejected_instead_of_silently_corrected(tmp_path):
    draft = spec(.23)
    draft['faults'][0]['onset_time_s'] = 0.
    source = tmp_path/'wrong-time-draft.json'
    source.write_text(json.dumps(draft),encoding='utf-8')
    declared = {'faults':[{'id':'f1','target':'whole_spacecraft','effect':'eps_battery_capacity_loss',
                            'start_s':7.,'magnitude':.23}]}
    result = run_capability_script_generation(CapabilityAgentRequest(
        request='Create a fault simulation with '+CID+'; fault at 7 seconds. Events: '+json.dumps(declared),
        draft_spec_path=source, output_dir=tmp_path/'out', max_repair_attempts=0, allowed_capabilities=(CID,)))
    assert any(issue.code == 'USER_EVENT_REQUIREMENT_MISMATCH' for issue in result.validation.errors)
    assert result.script is None


def test_malformed_explicit_onset_is_a_validation_error_not_a_coercion_crash(tmp_path):
    draft = spec(.23)
    draft['faults'][0]['onset_time_s'] = 'bad'
    source = tmp_path/'bad-time-draft.json'
    source.write_text(json.dumps(draft),encoding='utf-8')
    result = run_capability_script_generation(CapabilityAgentRequest(
        request='Create a fault simulation with '+CID, draft_spec_path=source,
        output_dir=tmp_path/'out', max_repair_attempts=0, allowed_capabilities=(CID,)))
    assert not result.validation.ok and result.script is None
