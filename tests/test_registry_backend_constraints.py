"""Execution binding comes from the registry, never a held-out label."""
import json

import pytest

from sat_sim.capability_agent import build_capability_json_prompt
from sat_sim.scenario_templates import instantiate_scenario_template
from sat_sim.task_validator import validate_task_spec
from sat_sim_ml.evaluation import registry_bound_messages


CAPABILITY = 'whole_spacecraft.maneuver_orbit_attitude.v1'


def test_python_maneuver_cannot_claim_basilisk_assembly_backend():
    spec = instantiate_scenario_template('maneuver_orbit_attitude')
    assert validate_task_spec(spec).ok
    spec['simulation']['backend'] = 'selective_unified_basilisk_assembly'
    result = validate_task_spec(spec)
    assert not result.ok
    assert any(item.code == 'BACKEND_CAPABILITY_MISMATCH' for item in result.errors)


def test_registry_prompt_binding_has_no_reference_output_argument():
    messages = [{'role':'system','content':'Generate JSON.'},
                {'role':'user','content':f'Create {CAPABILITY} for 120 seconds.'}]
    saved = json.loads(json.dumps(messages))
    augmented, evidence = registry_bound_messages(messages, CAPABILITY)
    assert messages == saved
    assert augmented[0] == saved[0]
    assert augmented[1]['content'].startswith(saved[1]['content'])
    assert 'simulation.backend' in augmented[1]['content']
    assert evidence['accepted_task_backends'] == ['python']
    assert len(evidence['contract_sha256']) == 64
    assert evidence['source'].endswith('not_reference_label')
    # The same contract is visible to the production model prompt.
    prompt = build_capability_json_prompt(f'Create {CAPABILITY}', allowed_capabilities=(CAPABILITY,))
    assert 'accepted_task_backends' in prompt and 'python' in prompt


def test_assistant_labels_and_unmentioned_capability_cannot_enter_binding():
    with pytest.raises(ValueError, match='ONLY_SYSTEM_AND_USER'):
        registry_bound_messages([{'role':'system','content':'test'},
            {'role':'user','content':CAPABILITY},{'role':'assistant','content':'label'}],CAPABILITY)
    with pytest.raises(ValueError, match='MUST_BE_PRESENT_IN_INPUT'):
        registry_bound_messages([{'role':'system','content':'test'},
            {'role':'user','content':'Create an unspecified case.'}],CAPABILITY)
