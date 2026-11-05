"""Explicit optional parameter additions from registered model candidates.

These are deterministic control-flow contracts; they do not execute native physics.
"""
from copy import deepcopy

import pytest

from sat_sim.form_schema import capability_form_schema
from sat_sim.task_models import canonicalize_task_spec, to_runtime_task_spec
from sat_sim.capability_agent import CapabilityAgentRequest, _validate_generated_draft
from sat_sim.capability_registry import get_capability
from sat_sim.unified_agent import _merge_natural_language_refinement, normalize_form_task_spec


CAPABILITY = "subsystem.eps.unified_native.v1"
INSTRUCTION = "Set solar_efficiency=0.3; preserve other parameters."


def _base():
    form = deepcopy(capability_form_schema(CAPABILITY)["default_form"])
    base = normalize_form_task_spec(form)
    for key in ("solar_efficiency", "comm_power_w"):
        base["parameters"]["values"].pop(key, None)
        base["model"].setdefault("config", {}).pop(key, None)
    return canonicalize_task_spec(base)


def _candidate(base, *, solar=0.3, include_solar=True):
    candidate = deepcopy(base)
    values = candidate["parameters"]["values"]
    if include_solar:
        values["solar_efficiency"] = solar
    values["comm_power_w"] = 27.0
    values["initial_soc"] = 0.9
    values["unregistered_drift"] = 123.0
    return candidate


def test_explicit_optional_parameter_addition_keeps_only_requested_registered_value():
    base = _base()
    candidate = _candidate(base)
    base_before, candidate_before = deepcopy(base), deepcopy(candidate)

    merged, changed = _merge_natural_language_refinement(base, candidate, INSTRUCTION)

    assert merged["parameters"]["values"]["solar_efficiency"] == 0.3
    assert to_runtime_task_spec(merged)["parameters"]["solar_efficiency"] == 0.3
    assert "parameters.values.solar_efficiency" in changed
    assert "comm_power_w" not in merged["parameters"]["values"]
    assert merged["parameters"]["values"]["initial_soc"] == 0.62
    assert "unregistered_drift" not in merged["parameters"]["values"]
    validation = _validate_generated_draft(
        merged, CapabilityAgentRequest(request=INSTRUCTION, compile_if_valid=False)
    )
    assert validation.ok, validation.errors
    parameters = get_capability(CAPABILITY).data["parameters"]
    assert parameters["solar_efficiency"]["required"] is False
    assert parameters["comm_power_w"]["required"] is False
    assert base == base_before
    assert candidate == candidate_before


def test_unrequested_registered_optional_candidate_addition_is_ignored():
    base = _base()
    candidate = _candidate(base)
    merged, changed = _merge_natural_language_refinement(
        base, candidate, "Preserve all parameters."
    )
    assert "solar_efficiency" not in merged["parameters"]["values"]
    assert "comm_power_w" not in merged["parameters"]["values"]
    assert not any(path.startswith("parameters.values.") for path in changed)


def test_unregistered_candidate_key_is_ignored_even_when_explicitly_named():
    base = _base()
    candidate = _candidate(base)
    merged, changed = _merge_natural_language_refinement(
        base, candidate, "Set unregistered_drift=123."
    )
    assert "unregistered_drift" not in merged["parameters"]["values"]
    assert "parameters.values.unregistered_drift" not in changed


def test_new_parameter_from_another_registered_capability_is_not_added():
    adcs_form = capability_form_schema("subsystem.adcs_unified_native.v1")["default_form"]
    candidate = normalize_form_task_spec(deepcopy(adcs_form))
    candidate["parameters"]["values"]["controller_k"] = 4.2

    base = _base()
    merged, changed = _merge_natural_language_refinement(
        base, candidate, "Set controller_k=4.2."
    )

    assert merged["model"]["capability_id"] == CAPABILITY
    assert "controller_k" not in merged["parameters"]["values"]
    assert "parameters.values.controller_k" not in changed


@pytest.mark.parametrize("include_solar, candidate_value", [(False, 0.3), (True, 0.4)])
def test_missing_or_wrong_model_value_is_rejected_by_final_explicit_guard(include_solar, candidate_value):
    base = _base()
    candidate = _candidate(
        base, solar=candidate_value, include_solar=include_solar
    )
    merged, _ = _merge_natural_language_refinement(base, candidate, INSTRUCTION)
    validation = _validate_generated_draft(
        merged, CapabilityAgentRequest(request=INSTRUCTION, compile_if_valid=False)
    )
    assert "USER_PARAMETER_REQUIREMENT_MISMATCH" in {
        issue.code for issue in validation.errors
    }
    assert any("solar_efficiency" in issue.message for issue in validation.errors)
