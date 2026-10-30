"""Generation spelling is canonical; parser compatibility is unchanged."""
import copy
import pytest
from jsonschema import Draft202012Validator, ValidationError
from sat_sim.llm_backends import taskspec_draft_response_schema
from sat_sim.task_models import canonicalize_task_spec

CID = 'whole_spacecraft.unified_native.v1'
GUIDED = Draft202012Validator(taskspec_draft_response_schema(CID, single_fault=True))
GENERAL = Draft202012Validator(taskspec_draft_response_schema(CID, constrain_events=True))


def draft():
    return {'schema_version':'0.1.0', 'task_id':'field_fixture', 'task_type':'whole_spacecraft',
            'capability_id':CID, 'target':{'level':'whole_spacecraft','name':'whole_spacecraft','mode':'fault'},
            'simulation':{'duration_s':20,'sample_s':1,'backend':'basilisk'}, 'outputs':{},
            'faults':[{'fault_id':'f1','fault_type':'eps_battery_capacity_loss','target':'whole_spacecraft',
                       'onset_time_s':7.,'duration_s':-1.,'magnitude':.23}]}


@pytest.mark.parametrize('alias,value', [('severity',.61), ('modifier_type','payload_instrument_off'),
                                       ('type','payload_instrument_off'), ('start_time_s',8.)])
def test_generated_duplicate_aliases_are_rejected_without_rewriting(alias,value):
    spec = draft()
    spec['faults'][0][alias] = value
    before = copy.deepcopy(spec)
    with pytest.raises(ValidationError):
        GUIDED.validate(spec)
    assert spec == before
    GENERAL.validate(spec)


def test_legacy_severity_alias_still_parses_outside_normal_form_generation():
    spec = draft()
    spec['faults'][0]['severity'] = spec['faults'][0].pop('magnitude')
    GENERAL.validate(spec)
    assert canonicalize_task_spec(spec)['events']['faults'][0]['magnitude'] == .23


@pytest.mark.parametrize('magnitude', [.11,.67])
def test_normal_form_generation_does_not_supply_the_answer(magnitude):
    spec = draft()
    spec['faults'][0]['magnitude'] = magnitude
    GUIDED.validate(spec)
    assert canonicalize_task_spec(spec)['events']['faults'][0]['magnitude'] == magnitude


def test_registered_target_is_grounded_in_generation_without_post_answer_replacement():
    spec = draft()
    spec['faults'][0]['target'] = 'eps'
    with pytest.raises(ValidationError):
        GUIDED.validate(spec)
    spec['faults'][0]['target'] = 'whole_spacecraft'
    GUIDED.validate(spec)


def test_preferred_generation_carrier_keeps_general_transport_compatibility():
    spec = draft()
    spec['modifiers'] = {'faults':spec.pop('faults')}
    GUIDED.validate(spec)
    preferred = Draft202012Validator(taskspec_draft_response_schema(CID,single_fault=True,single_fault_carrier='faults'))
    with pytest.raises(ValidationError):
        preferred.validate(spec)
    canonical = canonicalize_task_spec(spec)
    preferred.validate(canonical)
