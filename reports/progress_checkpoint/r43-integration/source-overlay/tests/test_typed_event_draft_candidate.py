"""Protocol/guard tests only; never count fixtures as model performance."""
import copy
import json
from pathlib import Path

import jsonschema
import pytest

from sat_sim.llm_backends import taskspec_draft_response_schema, LLMCallConfig
from sat_sim.capability_agent import CapabilityAgentRequest, TextModelTaskSpecBackend

CID='whole_spacecraft.unified_native.v1'


def legacy():
    return {'schema_version':'0.1.0','task_id':'typed_event_protocol',
            'task_type':'whole_spacecraft','capability_id':CID,'target':{},
            'simulation':{},'outputs':{}}


@pytest.mark.parametrize('carrier',['faults','modifiers'])
@pytest.mark.parametrize('wrapper',[None,'task_spec','taskspec','TaskSpec','spec'])
def test_missing_discriminator_regression_is_rejected_but_original_transport_retained(carrier,wrapper):
    draft=legacy()
    malformed={'modifier_id':'d1','target':'eps.battery_capacity_loss',
               'onset_time_s':4.,'magnitude':.17,'severity':1.,'duration_s':-1.}
    draft[carrier]=[malformed] if carrier=='faults' else {'faults':[malformed]}
    payload={wrapper:draft} if wrapper else draft
    jsonschema.validate(payload,taskspec_draft_response_schema(CID))
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload,taskspec_draft_response_schema(CID,constrain_events=True))
    malformed['fault_type']='eps_battery_capacity_loss'
    jsonschema.validate(payload,taskspec_draft_response_schema(CID,constrain_events=True))
    assert malformed['magnitude']==.17 and malformed['onset_time_s']==4.


@pytest.mark.parametrize('key',['fault_type','modifier_type','type','parameters'])
def test_parser_supported_effect_discriminators_remain_available(key):
    draft=legacy()
    item={'modifier_id':'x','target':'whole_spacecraft','onset_time_s':4.,'magnitude':.17}
    item[key]={'scenario':'eps_battery_capacity_loss'} if key=='parameters' else 'eps_battery_capacity_loss'
    draft['modifiers']={'faults':[item]}
    schema=taskspec_draft_response_schema(CID,constrain_events=True)
    jsonschema.validate(draft,schema)
    if key=='parameters':item[key]['scenario']='invented_effect'
    else:item[key]='invented_effect'
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate(draft,schema)


@pytest.mark.parametrize('kind,effect',[('faults','eps_battery_capacity_loss'),
    ('degradations','thermal_radiator_rejection_loss'),('constraints','power_safe_mode_threshold')])
@pytest.mark.parametrize('wrapper',[None,'task_spec','taskspec','TaskSpec','spec'])
def test_canonical_event_categories_wrappers_and_values_remain_available(kind,effect,wrapper):
    item={'id':'event','target':'whole_spacecraft','effect':effect,'start_s':4.,'magnitude':.17}
    draft={'schema_version':'1.0.0','task':{},'simulation':{},'outputs':{},
           'model':{'capability_id':CID},'events':{kind:[item]}}
    payload={wrapper:draft} if wrapper else draft
    schema=taskspec_draft_response_schema(CID,constrain_events=True)
    jsonschema.validate(payload,schema)
    item['magnitude']=1.01
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate(payload,schema)


def test_explicit_no_event_constraints_still_dominate_typed_event_guidance():
    original=taskspec_draft_response_schema(CID,forbid_events=True)
    typed=taskspec_draft_response_schema(CID,forbid_events=True,constrain_events=True)
    assert original==typed


def test_positive_natural_fault_intent_reaches_the_real_backend_config():
    backend=TextModelTaskSpecBackend(name='protocol_only',config=LLMCallConfig(
        backend='openai_compatible',model='protocol_only',structured_output='json_schema'))
    request=CapabilityAgentRequest(request='Use whole_spacecraft.unified_native.v1; inject battery capacity loss fault at4 seconds, magnitude17%.',allowed_capabilities=(CID,))
    schema=backend._grounded_config(request).response_schema
    assert schema==taskspec_draft_response_schema(CID,constrain_events=True,
        allow_geometry_overrides=False,allow_per_wheel_overrides=False)
