"""Public prompts must carry the same executable contracts as validation."""
import json
import re

import yaml
import pytest

from sat_sim.capability_agent import _example_for_prompt, build_capability_json_prompt
from sat_sim.capability_registry import active_capability_ids, get_capability
from sat_sim.task_validator import validate_task_spec


@pytest.mark.parametrize(('text','expected'),[
    ('运行20秒，故障在5秒发生',{'duration_s':20.}),
    ('故障在20秒发生，运行60秒',{'duration_s':60.}),
    ('Create a 30-second internal engineering simulation',{'duration_s':30.}),
    ('simulate for 2 minutes',{'duration_s':120.}),
    ('运行1小时，每5秒采样',{'duration_s':3600.,'sample_s':5.}),
    ('只修改故障，使其持续20秒',{}),
    ('本轮补充指令：把仿真时长改成30秒',{'duration_s':30.}),
])
def test_explicit_user_time_does_not_bind_fault_onset(text,expected):
    from sat_sim.capability_agent import _explicit_time_requirements
    assert _explicit_time_requirements(text)==expected


def test_model_duration_mismatch_is_rejected_without_template_replacement(tmp_path):
    from sat_sim.capability_agent import CapabilityAgentRequest, run_capability_script_generation
    cid = 'subsystem.adcs_unified_native.v1'
    spec = yaml.safe_load(_example_for_prompt(cid))
    spec['simulation']['duration_s'] = 2.
    spec['simulation']['sample_s'] = .1
    source = tmp_path/'wrong-model-duration.json'
    source.write_text(json.dumps(spec),encoding='utf-8')
    result = run_capability_script_generation(CapabilityAgentRequest(
        request=f'使用 {cid} 创建ADCS正常仿真，运行20秒',draft_spec_path=source,output_dir=tmp_path/'out',
        allowed_capabilities=(cid,),max_repair_attempts=2))
    assert not result.ok
    assert result.compiled is None
    assert 'USER_TIME_REQUIREMENT_MISMATCH' in {issue.code for issue in result.validation.errors}
    assert result.task_spec['simulation']['duration_s']==2.
    from sat_sim.agent_facade import AgentFacadeRequest, run_agent
    facade = run_agent(AgentFacadeRequest(request=f'使用 {cid} 运行20秒',
        draft_spec_path=source,output_dir=tmp_path/'facade'))
    assert not facade.ok
    assert facade.compiled is None
    assert facade.script is None
    assert 'USER_TIME_REQUIREMENT_MISMATCH' in {issue.code for issue in facade.validation.errors}


def test_all_active_prompt_examples_pass_the_actual_validator():
    import jsonschema
    from sat_sim.llm_backends import taskspec_draft_response_schema
    for capability_id in active_capability_ids():
        spec = yaml.safe_load(_example_for_prompt(capability_id))
        jsonschema.validate(spec,taskspec_draft_response_schema())
        result = validate_task_spec(spec)
        assert result.ok,(capability_id,result.to_dict())
        from sat_sim.form_schema import capability_form_schema
        displayed = capability_form_schema(capability_id)['default_form']['outputs']
        assert spec['outputs']['qoi']==displayed['qoi']
        assert spec['outputs']['plots']==displayed['plots']


def test_compact_prompt_declares_outputs_backend_and_native_effects():
    cid = 'subsystem.adcs_unified_native.v1'
    prompt = build_capability_json_prompt('Create a nominal ADCS simulation',allowed_capabilities=(cid,))
    context = json.loads(re.search(r'## Capability context\s*```json\s*(.*?)\s*```',prompt,re.S).group(1))
    contract = context['selected_capability']
    assert contract['accepted_task_backends']==['basilisk']
    assert 'adcs_rw_jamming' in contract['effects']['faults']
    assert contract['outputs']['trace']
    assert contract['default_summary_qoi']
    assert 'copy default_summary_qoi exactly' in prompt
def test_json_transport_examples_demonstrate_json_only():
    import json
    import re
    from sat_sim.capability_agent import build_capability_json_prompt

    prompt = build_capability_json_prompt(
        '创建热节点仿真，运行30秒',
        allowed_capabilities=('component.thermal_node.v1',),
    )
    assert '```yaml' not in prompt
    assert 'Return one YAML/JSON object' not in prompt
    blocks = re.findall(r'```json\s*\n(.*?)```', prompt, re.DOTALL)
    assert len(blocks) >= 2
    assert all(isinstance(json.loads(block), dict) for block in blocks)



@pytest.mark.parametrize(('prompt_text','expected'),[
    ('Simulate a battery component and output battery SOC.','component.battery.v1'),
    ('Create an EPS subsystem simulation; output final battery SOC and minimum power margin.','subsystem.eps.basic.v1'),
    ('Create an EPS unified runtime simulation and output battery SOC.','subsystem.eps.unified_native.v1'),
    ('Use subsystem.eps.source_native.v1 for battery SOC simulation.','subsystem.eps.source_native.v1'),
])
def test_requested_eps_scope_is_not_replaced_by_output_component_keywords(prompt_text,expected):
    from sat_sim.capability_agent import DEFAULT_ALLOWED_CAPABILITIES
    from sat_sim.capability_planner import plan_capability_for_request
    result=plan_capability_for_request(prompt_text,allowed_capabilities=DEFAULT_ALLOWED_CAPABILITIES)
    assert result.supported
    assert result.selected_capability_id == expected
