"""Literal time bindings in the model transport contract, before normalization."""
import pytest
from jsonschema import Draft202012Validator

from sat_sim.llm_backends import taskspec_draft_response_schema


@pytest.mark.parametrize("canonical", [True, False])
@pytest.mark.parametrize("wrapped", [True, False])
def test_sample_period_is_independent_of_integration_step(canonical, wrapped):
    schema = taskspec_draft_response_schema(time_bindings={"step_s": 0.2, "sample_s": 1})
    validator = Draft202012Validator(schema)
    simulation = {"sample_s": 1, "step_s": 0.2} if canonical else {"sample_s": 1, "solver": {"step_s": 0.2}}
    draft = {"schema_version": "1.0.0", "task": {}} if canonical else {
        "schema_version": "0.1.0", "task_id": "test", "task_type": "whole_spacecraft", "target": {}}
    draft.update(simulation=simulation, outputs={})
    document = {"task_spec": draft} if wrapped else draft
    assert validator.is_valid(document)
    simulation["sample_s"] = 0.2
    assert not validator.is_valid(document)
    del simulation["sample_s"]
    assert not validator.is_valid(document)


def test_single_binding_leaves_unspecified_time_fields_available():
    schema = taskspec_draft_response_schema(time_bindings={"duration_s": 20})
    simulation = schema["anyOf"][0]["properties"]["simulation"]
    validator = Draft202012Validator(simulation)
    assert validator.is_valid({"duration_s": 20})
    assert validator.is_valid({"duration_s": 20, "sample_s": 0.5, "solver": {"step_s": 0.1}})
    assert not validator.is_valid({"duration_s": 30})
    assert taskspec_draft_response_schema()["anyOf"][0]["properties"]["simulation"] == {"type": "object"}
