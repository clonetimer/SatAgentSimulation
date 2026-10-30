"""Judge observed resource ratios against the requested loss, including aliases."""
import pytest

from sat_sim.effect_evidence import evaluate_runtime_effect, get_effect_evidence_profile


EFFECTS = [
    ('eps_battery_capacity_loss', 'remaining_capacity_ratio', 'eps.battery_capacity_ratio'),
    ('solar_panel_efficiency_loss', 'remaining_efficiency_ratio', 'eps.solar_array_efficiency_ratio'),
    ('thermal_radiator_rejection_loss', 'remaining_rejection_ratio', 'thermal.radiator_rejection_ratio'),
]


def evaluate(effect, field, event, observed):
    profile = get_effect_evidence_profile('whole_spacecraft.unified_native.v1', effect)
    row = {key: 1. for key in profile.evidence_fields}
    row.update({field: observed, 'time_s': 5., 'event.active_effects': effect,
                'label.fault_active': True, 'label.degradation_active': True})
    return evaluate_runtime_effect(capability_id=profile.capability_id, effect_id=effect,
        summary={}, rows=[row], delivery_ok=True, event=event)


@pytest.mark.parametrize('effect,parameter,field', EFFECTS)
@pytest.mark.parametrize('declaration,expected', [
    ({'magnitude': .3}, .7), ({'severity': .1}, .9),
    ({'magnitude': 0.}, 1.), ({'magnitude': 1.}, 0.),
    ({'parameters': {'remaining_ratio': .8}}, .8),
    ({'parameters': {'loss_fraction': .4}}, .6),
    ({'parameters': {'loss_pct': 20.}}, .8), ({}, .5),
])
def test_correct_resource_loss_passes_and_wrong_observation_fails(effect, parameter, field, declaration, expected):
    assert evaluate(effect, field, declaration, expected).result == 'PASS'
    wrong = .5 if expected != .5 else .7
    assert evaluate(effect, field, declaration, wrong).result == 'FAIL'


@pytest.mark.parametrize('effect,parameter,field', EFFECTS)
def test_explicit_resource_ratio_has_priority_over_scalar_and_generic_parameter(effect, parameter, field):
    event = {'magnitude': .3, 'parameters': {parameter: .85, 'remaining_ratio': .6, 'loss_fraction': .9}}
    assert evaluate(effect, field, event, .85).result == 'PASS'
    assert evaluate(effect, field, event, .7).result == 'FAIL'


@pytest.mark.parametrize('effect,parameter,field', EFFECTS)
def test_missing_or_wrong_effect_observation_cannot_pass(effect, parameter, field):
    result = evaluate_runtime_effect(capability_id='whole_spacecraft.unified_native.v1',
        effect_id=effect, summary={}, rows=[], delivery_ok=True, event={'magnitude': .3})
    assert result.result == 'INCONCLUSIVE'
