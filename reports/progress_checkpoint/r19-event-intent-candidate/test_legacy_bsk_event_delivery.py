"""Carrier equivalence and strict loss values at the real native parser."""
import copy
import math
import pytest
from sat_sim.bsk_engine.event_manager import parse_bsk_events


def fault():
    return {'fault_id':'f1','fault_type':'eps_battery_capacity_loss','target':'whole_spacecraft',
            'onset_time_s':4.,'duration_s':-1.,'magnitude':.23}


def test_legacy_fault_reaches_identical_native_event_and_loss_parameter():
    item = fault()
    legacy = {'faults':[item]}
    modifier = {'modifiers':{'faults':[{**item,'modifier_id':'f1'}]}}
    canonical = {'events':{'faults':[{'id':'f1','effect':'eps_battery_capacity_loss',
                    'target':'whole_spacecraft','start_s':4.,'magnitude':.23}]}}
    before = copy.deepcopy(legacy)
    assert parse_bsk_events(legacy) == parse_bsk_events(modifier) == parse_bsk_events(canonical)
    event = parse_bsk_events(legacy)[0]
    assert event.event_id == 'f1' and event.start_s == 4. and event.end_s is None
    assert math.isclose(event.parameters['remaining_ratio'],.77,abs_tol=1e-15)
    assert legacy == before


@pytest.mark.parametrize('kind,effect', [('degradations','solar_panel_efficiency_loss'),
                                       ('constraints','power_safe_mode_threshold')])
def test_top_level_typed_event_arrays_are_not_ignored(kind,effect):
    item = {'id':'e1','effect':effect,'target':'whole_spacecraft','start_s':4.,'magnitude':.23}
    assert parse_bsk_events({kind:[item]}) == parse_bsk_events({'events':{kind:[item]}})


def test_mirrored_identical_event_is_applied_once():
    item = fault()
    spec = {'faults':[item],'modifiers':{'faults':[copy.deepcopy(item)]}}
    assert len(parse_bsk_events(spec)) == 1


@pytest.mark.parametrize('value', [-.2,17.,float('nan'),float('inf'),True])
def test_legacy_loss_domain_is_rejected_not_clamped(value):
    item = fault()
    item['magnitude'] = value
    with pytest.raises(ValueError,match='LOSS_MAGNITUDE_MUST_BE_FINITE_FRACTION_IN_0_1'):
        parse_bsk_events({'faults':[item]})
