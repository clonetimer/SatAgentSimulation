"""Public prompts must carry the same executable contracts as validation."""
import json
import re

import yaml
import pytest

from sat_sim.capability_agent import _example_for_prompt, build_capability_agent_prompt, build_capability_json_prompt
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


@pytest.mark.parametrize("compact", [True, False], ids=["compact", "full"])
@pytest.mark.parametrize("backend", ["project_reference", "basilisk_native"])
def test_thermal_prompt_uses_the_requested_profile(compact, backend):
    capability = "component.thermal_node.v1"
    request = "Create a nominal thermal node simulation"
    if backend == "basilisk_native":
        request += " with physics_backend=basilisk_native, output thermal.node.input_power_w"
    prompt = build_capability_agent_prompt(
        request, allowed_capabilities=("component.battery.v1", capability), compact=compact,
    )
    context = json.loads(re.search(r"## Capability context\s*```json\s*(.*?)\s*```", prompt, re.S).group(1))
    contract = (context["selected_capability"] if compact else
                next(item for item in context["capability_contracts"] if item["capability_id"] == capability))
    assert contract["parameters"]["physics_backend"]["default"] == backend
    other_backend = "project_reference" if backend == "basilisk_native" else "basilisk_native"
    profiles = {backend: contract, other_backend: contract["backend_profiles"][other_backend]}
    for name, profile in profiles.items():
        trace = {field if isinstance(field, str) else field["name"] for field in profile["outputs"]["trace"]}
        assert ("mass_kg" in profile["parameters"]) == (name == "basilisk_native")
        assert ("ambient_k" in profile["parameters"]) == (name == "project_reference")
        assert ("thermal.node.input_power_w" in trace) == (name == "basilisk_native")
        assert ("thermal.node.effective_tau_s" in trace) == (name == "project_reference")
    assert context["requested_output_fields"] == (["thermal.node.input_power_w"] if backend == "basilisk_native" else [])
    example = yaml.safe_load(re.search(r"## Capability-specific example\s*```yaml\s*(.*?)\s*```", prompt, re.S).group(1))
    if backend == "basilisk_native":
        assert example["parameters"]["physics_backend"] == backend
        pytest.importorskip("Basilisk", reason="native thermal validation requires Basilisk")
    result = validate_task_spec(example)
    assert result.ok, result.to_dict()


def test_thermal_template_agent_accepts_an_explicit_native_backend(tmp_path):
    pytest.importorskip("Basilisk", reason="native thermal generation requires Basilisk")
    from sat_sim.capability_agent import CapabilityAgentRequest, run_capability_script_generation

    result = run_capability_script_generation(CapabilityAgentRequest(
        request="Create a nominal thermal node simulation with physics_backend=basilisk_native, power_w=12, "
                "and output thermal.node.input_power_w",
        allowed_capabilities=("component.thermal_node.v1",),
        output_dir=tmp_path,
    ))
    assert result.ok, result.validation.to_dict()
    assert result.script is not None
    assert result.task_spec["parameters"]["physics_backend"] == "basilisk_native"
    assert result.task_spec["parameters"]["power_w"] == 12
    assert "thermal.node.input_power_w" in result.task_spec["outputs"]["plots"]


@pytest.mark.parametrize("scope,capability", [
    ("thermal subsystem", "subsystem.thermal.basic_lumped.v1"),
    ("whole spacecraft", "whole_spacecraft.unified_native.v1"),
])
def test_thermal_node_mention_keeps_explicit_larger_template_scope(scope, capability):
    from sat_sim.capability_agent import CapabilityTemplateBackend

    selection = CapabilityTemplateBackend().select_template(f"Simulate the {scope} and report the thermal node temperature", {})
    assert selection.capability_id == capability


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
