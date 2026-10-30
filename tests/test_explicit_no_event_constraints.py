"""Explicit no-event intent constrains generation and final validation."""
import copy
import json

import jsonschema
import pytest
import yaml

from sat_sim.capability_agent import (
    CapabilityAgentRequest, TextModelTaskSpecBackend, _example_for_prompt,
    _request_forbids_all_events, _explicit_event_requirement_issues,
)
from sat_sim.llm_backends import LLMCallConfig, taskspec_draft_response_schema
from sat_sim.scenario_templates import instantiate_scenario_template

CID = 'whole_spacecraft.unified_native.v1'


@pytest.mark.parametrize('text', [
    '无故障、退化或约束事件。', '无故障、无退化、无约束。',
    'No faults, degradations or constraints.', 'without faults, degradations, and constraints',
])
def test_explicit_all_event_absence_is_recognized(text):
    assert _request_forbids_all_events(text)


@pytest.mark.parametrize('text', ['名义任务', '无故障，但需要退化',
    'No faults; add degradation.', '无故障、退化或约束事件。本轮补充指令：添加容量损失事件'])
def test_partial_absence_or_superseded_instruction_does_not_forbid_everything(text):
    assert not _request_forbids_all_events(text)


@pytest.mark.parametrize('kind', ['faults', 'degradations', 'constraints'])
@pytest.mark.parametrize('wrapped', [False, True])
def test_no_event_schema_rejects_invented_modifiers(kind, wrapped):
    draft = yaml.safe_load(_example_for_prompt(CID))
    draft['modifiers'] = {kind: []}
    schema = taskspec_draft_response_schema(CID, forbid_events=True)
    payload = {'task_spec': draft} if wrapped else draft
    jsonschema.validate(payload, schema)
    draft['modifiers'][kind] = [{'effect': 'invented'}]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, schema)
    # Requests that permit events retain the original schema; no global ban.
    jsonschema.validate(payload, taskspec_draft_response_schema(CID))


def test_no_event_intent_reaches_the_actual_backend_schema():
    backend = TextModelTaskSpecBackend(name='protocol', config=LLMCallConfig(
        backend='openai_compatible', structured_output='json_schema'))
    request = CapabilityAgentRequest(request=f'Use {CID}. No faults, degradations or constraints.', allowed_capabilities=(CID,))
    assert backend._grounded_config(request).response_schema == taskspec_draft_response_schema(CID, forbid_events=True, allow_geometry_overrides=False, allow_per_wheel_overrides=False)


def test_valid_registered_event_is_rejected_when_the_user_requested_none():
    spec = instantiate_scenario_template('whole_battery_loss')
    before = copy.deepcopy(spec)
    issues = _explicit_event_requirement_issues(spec, 'No faults, degradations or constraints.')
    assert [x.code for x in issues] == ['USER_NO_EVENTS_MISMATCH']
    assert spec == before


def test_contradictory_explicit_event_declaration_is_not_silently_discarded():
    spec = instantiate_scenario_template('whole_battery_loss')
    text = 'No faults, degradations or constraints. Events: ' + json.dumps(spec['events'])
    assert [x.code for x in _explicit_event_requirement_issues(spec, text)] == ['USER_EVENT_REQUIREMENTS_CONFLICT']
