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
    first_attempt = json.loads((tmp_path/'out'/'first_attempt.json').read_text(encoding='utf-8'))
    assert first_attempt['validation_ok'] is False
    assert 'USER_TIME_REQUIREMENT_MISMATCH' in {
        issue['code'] for issue in first_attempt['validation']['errors']
    }
    assert first_attempt['pre_validation_corrections'] == []
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


def test_first_attempt_evidence_records_unrepaired_valid_draft(tmp_path):
    from sat_sim.capability_agent import CapabilityAgentRequest, run_capability_script_generation
    cid = 'subsystem.adcs_unified_native.v1'
    spec = yaml.safe_load(_example_for_prompt(cid))
    source = tmp_path/'valid-model-draft.json'
    source.write_text(json.dumps(spec),encoding='utf-8')
    result = run_capability_script_generation(CapabilityAgentRequest(
        request=f'使用 {cid} 创建ADCS正常仿真',
        draft_spec_path=source,
        output_dir=tmp_path/'valid-out',
        allowed_capabilities=(cid,),
        max_repair_attempts=2,
    ))
    assert result.ok
    first_attempt = json.loads((tmp_path/'valid-out'/'first_attempt.json').read_text(encoding='utf-8'))
    assert first_attempt['schema_version'] == 'sat-sim.generation-first-attempt.v1'
    assert first_attempt['validation_ok'] is True
    assert first_attempt['pre_validation_corrections'] == []
    assert first_attempt['draft_source'] == 'external_draft_file'
    assert result.files['first_attempt'].endswith('first_attempt.json')


def test_real_model_timeout_is_counted_as_failed_first_answer(monkeypatch, tmp_path):
    import sat_sim.capability_agent as capability_agent

    class TimeoutBackend:
        name = "openai_compatible"
        last_raw_output = ""
        last_call = None

        def draft_task_spec(self, *, request, context):
            raise TimeoutError("model request exceeded timeout")

    monkeypatch.setattr(
        capability_agent,
        "_backend_from_request",
        lambda request: TimeoutBackend(),
    )
    result = capability_agent.run_capability_script_generation(
        capability_agent.CapabilityAgentRequest(
            request=(
                "Create a nominal reaction wheel simulation using "
                "component.reaction_wheel.v1."
            ),
            output_dir=tmp_path / "timeout-out",
            backend="openai_compatible",
            allowed_capabilities=("component.reaction_wheel.v1",),
            max_repair_attempts=0,
        )
    )
    assert not result.ok
    first_attempt = json.loads(
        (tmp_path / "timeout-out" / "first_attempt.json").read_text(encoding="utf-8")
    )
    assert first_attempt["first_answer_eligible"] is True
    assert first_attempt["first_answer_pass"] is False
    assert first_attempt["eligible_unrepaired_first_answer"] is True
    assert first_attempt["validation_ok"] is False
    assert first_attempt["draft_parse_or_schema_failed"] is True
    assert first_attempt["failure_type"] == "TimeoutError"


def _battery_experience_outline(*, capability_id="component.battery.v1"):
    return {
        "schema_version": "1.0.0",
        "model": {
            "capability_id": capability_id,
            "target": {
                "level": "component",
                "name": "battery",
                "mode": "nominal",
            },
        },
        "simulation": {
            "duration_s": 12.0,
            "sample_s": 2.0,
            "backend": "historical-backend-must-not-override-registry",
        },
        "parameters": {
            "values": {
                "capacity_wh": 260.0,
                "initial_soc": 0.67,
            },
        },
        "events": {"faults": [], "degradations": [], "constraints": []},
        "outputs": {
            "qoi": [],
            "plots": [],
        },
    }


def test_experience_example_preserves_registry_qoi_plots_and_backend():
    cid = "component.battery.v1"
    base = yaml.safe_load(_example_for_prompt(cid, omit_optional_parameters=True))
    outline = _battery_experience_outline()
    prompt = build_capability_json_prompt(
        "Create a nominal battery simulation for 12 seconds.",
        allowed_capabilities=(cid,),
        advisory_context=(
            {
                "lesson_id": "lesson_test",
                "lesson_sha256": "a" * 64,
                "advisory": {"verified_taskspec_outlines": [outline]},
            },
        ),
    )
    blocks = re.findall(r"\x60\x60\x60json\s*\n(.*?)\x60\x60\x60", prompt, re.DOTALL)
    assert len(blocks) >= 2
    example = json.loads(blocks[-1])

    assert example["capability_id"] == cid
    assert example["parameters"]["capacity_wh"] == 260.0
    assert example["parameters"]["initial_soc"] == 0.67
    assert example["simulation"]["duration_s"] == 12.0
    assert example["simulation"]["sample_s"] == 2.0
    assert example["simulation"]["backend"] == base["simulation"]["backend"]
    assert example["outputs"]["qoi"] == base["outputs"]["qoi"]
    assert example["outputs"]["plots"] == base["outputs"]["plots"]
    assert example["outputs"]["plots"], "registry plot example must not be erased by lesson reuse"


def test_experience_outline_from_another_capability_cannot_replace_registry_example():
    cid = "component.battery.v1"
    base = yaml.safe_load(_example_for_prompt(cid, omit_optional_parameters=True))
    outline = _battery_experience_outline(capability_id="component.reaction_wheel.v1")
    prompt = build_capability_json_prompt(
        "Create a nominal battery simulation.",
        allowed_capabilities=(cid,),
        advisory_context=(
            {"advisory": {"verified_taskspec_outlines": [outline]}},
        ),
    )
    blocks = re.findall(r"\x60\x60\x60json\s*\n(.*?)\x60\x60\x60", prompt, re.DOTALL)
    example = json.loads(blocks[-1])
    assert example["capability_id"] == cid
    assert example["outputs"]["qoi"] == base["outputs"]["qoi"]
    assert example["outputs"]["plots"] == base["outputs"]["plots"]
