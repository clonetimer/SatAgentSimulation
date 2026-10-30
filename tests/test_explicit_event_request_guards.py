"""User-declared events must survive generation, normalization and repair."""
import copy
import json

import pytest

from sat_sim.capability_agent import (
    CapabilityAgentRequest, _explicit_event_requirement_issues,
    run_capability_script_generation,
)
from sat_sim.scenario_templates import instantiate_scenario_template


def _case():
    spec = instantiate_scenario_template('whole_battery_loss')
    events = copy.deepcopy(spec['events'])
    event = events['faults'][0]
    event['magnitude'] = .3
    spec['events'] = copy.deepcopy(events)
    request = ('Use whole_spacecraft.composite_digital_twin.v1. Events: '
               + json.dumps(events) + '. Keep the registered configuration.')
    return spec, request


def test_exact_declared_events_pass_without_mutating_the_draft():
    spec, request = _case()
    before = copy.deepcopy(spec)
    assert not _explicit_event_requirement_issues(spec, request)
    assert spec == before


@pytest.mark.parametrize(('field','value'),[
    ('magnitude',1.), ('start_s',25.), ('target','component.battery'),
    ('effect','payload_instrument_off'), ('delivery','runtime_parameter'),
])
def test_changed_user_event_semantics_are_rejected(field,value):
    spec, request = _case()
    spec['events']['faults'][0][field] = value
    issues = _explicit_event_requirement_issues(spec, request)
    assert issues
    assert all(issue.code == 'USER_EVENT_REQUIREMENT_MISMATCH' for issue in issues)


@pytest.mark.parametrize('mutation',['missing','extra','duplicate'])
def test_missing_extra_and_duplicate_events_are_rejected(mutation):
    spec, request = _case()
    events = spec['events']['faults']
    if mutation == 'missing':
        events.clear()
    else:
        added = copy.deepcopy(events[0])
        if mutation == 'extra':
            added['id'] = 'not-requested'
        events.append(added)
    assert _explicit_event_requirement_issues(spec, request)


def test_explicit_zero_events_forbids_inventing_a_fault():
    spec, _request = _case()
    assert _explicit_event_requirement_issues(spec, 'Events: {}')


@pytest.mark.parametrize('block',['{bad JSON}','[]','{"faults":[{}]}'])
def test_invalid_explicit_event_json_requires_correction(block):
    spec, _request = _case()
    issues = _explicit_event_requirement_issues(spec, 'Events: '+block)
    assert issues[0].code == 'USER_EVENT_REQUIREMENTS_INVALID'


def test_latest_refinement_can_explicitly_replace_the_prior_events():
    spec, request = _case()
    spec['events'] = {'faults':[],'degradations':[],'constraints':[]}
    spec['model']['target']['mode'] = 'nominal'
    assert not _explicit_event_requirement_issues(spec, request+'\n本轮补充指令：Events: {}')


def test_nested_user_parameters_cannot_be_omitted_or_changed():
    spec, request = _case()
    declared = copy.deepcopy(spec['events'])
    declared['faults'][0]['parameters'] = {'operator_note':{'mode':'requested'}}
    request = 'Events: '+json.dumps(declared)
    assert _explicit_event_requirement_issues(spec, request)
    spec['events'] = declared
    assert not _explicit_event_requirement_issues(spec, request)
    spec['events']['faults'][0]['parameters']['operator_note']['mode'] = 'different'
    assert _explicit_event_requirement_issues(spec, request)


def test_real_generation_boundary_never_compiles_or_locally_replaces_wrong_magnitude(tmp_path):
    spec, request = _case()
    spec['events']['faults'][0]['magnitude'] = 1.
    source = tmp_path/'wrong-model-response.json'
    source.write_text(json.dumps(spec),encoding='utf-8')
    result = run_capability_script_generation(CapabilityAgentRequest(
        request=request,draft_spec_path=source,output_dir=tmp_path/'out',
        allowed_capabilities=('whole_spacecraft.composite_digital_twin.v1',),
        max_repair_attempts=2))
    assert not result.ok
    assert result.compiled is None
    assert result.script is None
    assert 'USER_EVENT_REQUIREMENT_MISMATCH' in {issue.code for issue in result.validation.errors}
    assert source.read_text(encoding='utf-8') == json.dumps(spec)


def test_complete_canonical_modifier_fault_is_projected_once_and_compiles(tmp_path):
    from sat_sim.task_models import canonicalize_task_spec
    spec, request = _case()
    source = tmp_path/'canonical-response.json'
    source.write_text(json.dumps(spec),encoding='utf-8')
    result = run_capability_script_generation(CapabilityAgentRequest(
        request=request,draft_spec_path=source,output_dir=tmp_path/'out',
        allowed_capabilities=('whole_spacecraft.composite_digital_twin.v1',),
        max_repair_attempts=0))
    assert result.ok, result.validation.to_dict()
    assert result.compiled is not None
    events = canonicalize_task_spec(result.task_spec)['events']['faults']
    assert len(events)==1
    assert events[0]['id']==spec['events']['faults'][0]['id']
    assert events[0]['delivery']=='modifier'
    assert events[0]['magnitude']==.3
