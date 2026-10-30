"""Production prompt-path checks; no fixture is counted as model accuracy."""
import copy
import json
import pytest
from sat_sim.capability_agent import build_capability_json_prompt
from sat_sim.form_schema import capability_form_schema
from sat_sim.task_models import canonicalize_task_spec
from sat_sim.unified_agent import UnifiedAgentRequest, normalize_form_task_spec, run_unified_agent


def test_real_refinement_prompt_omits_machine_histories_but_preserves_physics_and_input(monkeypatch, tmp_path):
    import sat_sim.unified_agent as module
    base = canonicalize_task_spec(normalize_form_task_spec(
        capability_form_schema('whole_spacecraft.unified_native.v1')['default_form']))
    base['parameters']['values']['battery_capacity_wh'] = 137.
    base['provenance']['fields']['parameters.values.battery_capacity_wh'] = {
        'source': 'user_explicit', 'evidence': 'PROVENANCE_SENTINEL_' + 'x' * 14000}
    base['metadata']['projection_test'] = 'METADATA_SENTINEL'
    before = copy.deepcopy(base)
    captured = {}
    class PromptReached(Exception):
        pass
    def stop_at_model_boundary(request):
        captured['request'] = request.request
        raise PromptReached()
    monkeypatch.setattr(module, 'run_agent', stop_at_model_boundary)
    with pytest.raises(PromptReached):
        run_unified_agent(UnifiedAgentRequest(input_kind='natural_language',
            request_text='保留其他配置并输出电池容量。', base_task_spec=base,
            backend='template', output_dir=tmp_path / 'prompt-probe', experience_reuse_enabled=False))
    prompt = captured['request']
    projected, _ = json.JSONDecoder().raw_decode(prompt.split('现有 TaskSpec：', 1)[1].lstrip())
    assert 'PROVENANCE_SENTINEL' not in prompt and 'METADATA_SENTINEL' not in prompt
    for key in ('parameters', 'events', 'simulation', 'model', 'outputs', 'assurance', 'mission'):
        assert projected[key] == before[key]
    assert base == before
    assert len(json.dumps(base)) - len(json.dumps(projected)) > 14000


def test_loss_fraction_instruction_teaches_units_without_binding_any_answer():
    prompt = build_capability_json_prompt('只添加一个故障。',
        allowed_capabilities=('whole_spacecraft.unified_native.v1',))
    assert 'P/100 as magnitude' in prompt
    assert 'not the remaining fraction' in prompt
    assert 'not an exponent-shifted approximation' in prompt
