"""JSON transport must not ask the model for YAML on draft or repair."""
import json

import pytest

from sat_sim.capability_agent import CapabilityAgentRequest, TextModelTaskSpecBackend
from sat_sim.llm_backends import LLMCallConfig, LLMCallResult
from sat_sim.scenario_templates import instantiate_scenario_template
from sat_sim.task_validator import validate_task_spec


@pytest.mark.parametrize('mode',['json_object','json_schema'])
def test_first_draft_and_repair_use_json_only_contract(monkeypatch, mode):
    spec = instantiate_scenario_template('adcs_unified_native')
    prompts = []

    class ProtocolBackend:
        def generate_text(self, prompt):
            prompts.append(prompt)
            return LLMCallResult(True,'protocol-fixture',json.dumps(spec),len(prompt),{'fixture':True})

    monkeypatch.setattr('sat_sim.capability_agent.make_text_llm_backend',lambda config:ProtocolBackend())
    backend = TextModelTaskSpecBackend(name='protocol-fixture',config=LLMCallConfig(
        backend='openai_compatible',model='protocol-fixture',structured_output=mode))
    request = CapabilityAgentRequest(request='Create an ADCS simulation for 60 seconds.',
        allowed_capabilities=('subsystem.adcs_unified_native.v1',),model_structured_output=mode)
    backend.draft_task_spec(request=request,context={})
    invalid = json.loads(json.dumps(spec))
    invalid['simulation']['duration_s'] = -1
    backend.draft_task_spec_with_validation_errors(request=request,context={},
        previous_draft=invalid,validation=validate_task_spec(invalid))
    assert len(prompts) == 2
    for prompt in prompts:
        assert 'Return one JSON object' in prompt
        assert 'Return one YAML/JSON object' not in prompt
        assert 'corrected JSON/YAML' not in prompt
        assert '```yaml' not in prompt
        assert 'Return valid json only.' in prompt


@pytest.mark.parametrize('shape',['legacy','canonical','task_spec','taskspec','TaskSpec','spec'])
def test_transport_accepts_supported_draft_shapes_before_semantic_validation(shape):
    import yaml
    import jsonschema
    from sat_sim.capability_agent import _example_for_prompt, extract_task_spec_from_model_text
    from sat_sim.llm_backends import taskspec_draft_response_schema
    spec = instantiate_scenario_template('adcs_unified_native') if shape == 'canonical' else yaml.safe_load(
        _example_for_prompt('subsystem.adcs_unified_native.v1'))
    if shape != 'canonical':
        # These fields are accepted by the legacy normalizer and must not
        # cause a transport 422 before that validator can inspect the draft.
        spec.update(mission={},validation={},assurance={})
        spec.pop('capability_id',None)
    wrapped = {shape:spec} if shape not in ('legacy','canonical') else spec
    jsonschema.validate(wrapped,taskspec_draft_response_schema())
    assert extract_task_spec_from_model_text(json.dumps(wrapped)) == spec


@pytest.mark.parametrize('invalid',[{},[],{'schema_version':'1.0.0'},
    {'task_spec':{}}, {'task_spec':{'simulation':{}}}])
def test_transport_rejects_non_drafts(invalid):
    import jsonschema
    from sat_sim.llm_backends import taskspec_draft_response_schema
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(invalid,taskspec_draft_response_schema())


def test_transport_schema_does_not_authorize_invalid_simulation():
    import jsonschema
    from sat_sim.llm_backends import taskspec_draft_response_schema
    spec = instantiate_scenario_template('adcs_unified_native')
    spec['simulation']['duration_s']=-1
    jsonschema.validate(spec,taskspec_draft_response_schema())
    assert not validate_task_spec(spec).ok


def test_http_request_uses_the_supported_draft_envelope(monkeypatch):
    from sat_sim.llm_backends import OpenAIChatCompletionsBackend, taskspec_draft_response_schema
    requests=[]
    class Response:
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def read(self):
            return json.dumps({'choices':[{'message':{'content':'{}'}}]}).encode()
    def urlopen(request,**kwargs):
        requests.append(json.loads(request.data))
        return Response()
    monkeypatch.setattr('sat_sim.llm_backends.urllib.request.urlopen',urlopen)
    result=OpenAIChatCompletionsBackend(model='protocol-fixture',base_url='http://127.0.0.1:9999/v1',
        structured_output='json_schema').generate_text('Return JSON only.')
    assert result.ok
    assert len(requests)==1
    assert requests[0]['response_format']['json_schema']['schema']==taskspec_draft_response_schema()
