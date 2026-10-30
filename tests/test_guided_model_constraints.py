"""Schema/HTTP contract checks only; these fixtures are not model evidence."""
import copy
import io
import json
from urllib.error import HTTPError

import jsonschema
import pytest
from fastapi.testclient import TestClient

from sat_sim.llm_backends import (OpenAIChatCompletionsBackend, taskspec_draft_response_schema,
                                  _registry_parameter_type_schema)
from sat_sim_ml.guided_decoding import response_schema
from sat_sim_ml.serving import create_model_app

CAPABILITY = 'whole_spacecraft.unified_native.v1'


@pytest.mark.parametrize('canonical',[False,True])
def test_selected_capability_schema_rejects_unconsumed_parameter_paths(canonical):
    schema = taskspec_draft_response_schema(CAPABILITY)
    if canonical:
        draft = {'schema_version':'1.0.0','task':{},'simulation':{},'outputs':{},
                 'model':{'capability_id':CAPABILITY,'config':{}},
                 'parameters':{'values':{'thermal_earth_ir_w_m2':234.}}}
        values = draft['parameters']['values']
    else:
        draft = {'schema_version':'0.1.0','task_id':'t','task_type':'whole_spacecraft',
                 'target':{},'simulation':{},'outputs':{},'capability_id':CAPABILITY,
                 'parameters':{'thermal_earth_ir_w_m2':234.}}
        values = draft['parameters']
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(draft,schema)
    values['rocket_boot'] = {'enabled':True}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(draft,schema)
    values.pop('rocket_boot')
    values['thermal_earth_ir_w_m2'] = 'a nonnumeric value'
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(draft,schema)


def test_response_schema_rejects_external_refs_before_generation():
    class Engine:
        model_name = 'protocol-only'
        provenance = {'engine':'protocol_fixture'}
        called = False
        def generate(self,*args,**kwargs):
            self.called = True
            raise AssertionError('invalid schema must be rejected before generation')
    engine = Engine()
    with TestClient(create_model_app(engine)) as client:
        response = client.post('/v1/chat/completions',json={
            'model':engine.model_name,'messages':[{'role':'user','content':'x'}],
            'response_format':{'type':'json_schema','json_schema':{'schema':{
                '$ref':'https://example.invalid/schema'}}}})
    assert response.status_code==422 and not engine.called


def test_structured_schema_is_forwarded_without_replacing_raw_completion():
    class Engine:
        model_name = 'protocol-only'
        provenance = {'engine':'protocol_fixture'}
        def generate(self,messages,*,max_tokens,response_schema=None):
            self.schema = response_schema
            return {'text':'{"value":0.3}','prompt_tokens':1,'completion_tokens':1,
                    'elapsed_s':0.,'finish_reason':'stop'}
    engine = Engine()
    schema = {'type':'object','properties':{'value':{'type':'number'}},'required':['value']}
    original = copy.deepcopy(schema)
    with TestClient(create_model_app(engine)) as client:
        response = client.post('/v1/chat/completions',json={
            'model':engine.model_name,'messages':[{'role':'user','content':'x'}],
            'response_format':{'type':'json_schema','json_schema':{'schema':schema}}})
    assert response.status_code==200
    assert engine.schema==original and schema==original
    assert response.json()['choices'][0]['message']['content']=='{"value":0.3}'
    assert response.json()['sat_sim']['template_fallback_used'] is False


def test_invalid_model_json_is_not_retried_through_unconstrained_transport(monkeypatch):
    requests = []
    def reject(request,**kwargs):
        requests.append(json.loads(request.data))
        body = json.dumps({'detail':{'reason_code':'MODEL_OUTPUT_OR_CONTEXT_REJECTED'}}).encode()
        raise HTTPError(request.full_url,422,'invalid model JSON',{},io.BytesIO(body))
    monkeypatch.setattr('urllib.request.urlopen',reject)
    result = OpenAIChatCompletionsBackend(model='protocol-only',base_url='http://localhost:1',
        structured_output='json_schema').generate_text('test')
    assert not result.ok and len(requests)==1
    assert requests[0]['response_format']['type']=='json_schema'


def test_json_mode_has_a_real_object_constraint_and_text_mode_has_none():
    assert response_schema({'type':'json_object'})=={'type':'object'}
    assert response_schema(None) is None


@pytest.mark.parametrize('kind,valid,invalid',[
    ('array[number]', [0.,-.4,0.], 'not an array'),
    ('array[array[number]]', [[1.,2.],[3.,4.]], [['text']]),
    ('array[bool]', [True,False], [1]),
    ('array[string]', ['a'], [1]),
    ('vector3', [1.,2.,3.], [1.,2.]),
    ('vector3_or_number', .3, [1.,2.]),
    ('number_or_array[number]', [1.,2.], ['text']),
    ('array[number_or_array[number]]', [1.,[2.,3.]], [None]),
])
def test_registry_compound_types_retain_value_types(kind,valid,invalid):
    schema = _registry_parameter_type_schema(kind)
    jsonschema.validate(valid,schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(invalid,schema)


def test_native_antenna_array_has_a_typed_transport_schema():
    schema = taskspec_draft_response_schema(CAPABILITY)
    legacy = schema['anyOf'][0]
    antenna = legacy['properties']['parameters']['properties']['spacecraft_antenna_orientation_b']
    assert antenna=={'type':'array','items':{'type':'number'}}
    jsonschema.validate([0.,-.41421356237309503,0.],antenna)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(['invalid'],antenna)


def test_unknown_registry_type_is_rejected_before_inference():
    with pytest.raises(ValueError,match='UNSUPPORTED_REGISTRY_PARAMETER_TYPE'):
        _registry_parameter_type_schema('an unknown registry type')
