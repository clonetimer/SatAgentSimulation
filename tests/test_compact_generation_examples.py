"""Prompt examples should not make the model retype unrelated optional defaults."""
import json
import re
import yaml

from sat_sim.capability_agent import _example_for_prompt, build_capability_json_prompt
from sat_sim.capability_registry import active_capability_ids, get_capability
from sat_sim.task_validator import validate_task_spec


def test_every_compact_example_keeps_required_inputs_and_passes_actual_validation():
    for cid in active_capability_ids():
        compact = yaml.safe_load(_example_for_prompt(cid, omit_optional_parameters=True))
        full = yaml.safe_load(_example_for_prompt(cid))
        assert compact, cid
        declared = get_capability(cid).data.get('parameters', {})
        required = {key for key,value in declared.items() if value.get('required') is True}
        assert required <= set(compact.get('parameters', {})), cid
        assert set(compact.get('parameters', {})) <= required, cid
        result = validate_task_spec(compact)
        assert result.ok, (cid, result.to_dict())
        assert compact['outputs'] == full['outputs']


def test_compact_prompt_keeps_full_parameter_contract_and_requests_all_user_inputs():
    cid = 'whole_spacecraft.unified_native.v1'
    request = f'{cid} thermal_earth_ir_w_m2=241; thermal_earth_albedo=.35'
    prompt = build_capability_json_prompt(request, allowed_capabilities=(cid,))
    blocks = re.findall(r'```json\s*\n(.*?)```',prompt,re.S)
    context, example = map(json.loads, blocks[:2])
    declared = get_capability(cid).data['parameters']
    assert set(context['selected_capability']['parameters']) == set(declared)
    assert 'thermal_earth_ir_w_m2=241' in prompt
    assert 'every user-specified parameter' in prompt
    assert 'Never omit a requested parameter' in prompt
    assert len(example.get('parameters',{})) < 10
    assert len(example.get('parameters',{})) < len(declared)
    assert 'retain the supplied task' in prompt
