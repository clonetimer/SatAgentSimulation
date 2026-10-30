"""Scalar loss magnitude must reach the physical parameter-event controller."""
import copy

import pytest

from sat_sim.bsk_engine.event_manager import parse_bsk_events


@pytest.mark.parametrize('effect',[
    'eps_battery_capacity_loss','solar_panel_efficiency_loss','thermal_radiator_rejection_loss',
])
@pytest.mark.parametrize('legacy',[False,True])
@pytest.mark.parametrize('magnitude',[0.,.1,.3,1.])
def test_scalar_loss_magnitude_becomes_the_remaining_physical_ratio(effect,legacy,magnitude):
    category='faults' if effect=='eps_battery_capacity_loss' else 'degradations'
    if legacy:
        item={'modifier_id':'loss',category[:-1]+'_type':effect,'target':'whole_spacecraft',
              'onset_time_s':2.,'duration_s':-1.,'severity':magnitude,'parameters':{}}
        spec={'modifiers':{category:[item]}}
    else:
        item={'id':'loss','effect':effect,'target':'whole_spacecraft','start_s':2.,
              'magnitude':magnitude,'parameters':{}}
        spec={'events':{category:[item]}}
    before=copy.deepcopy(spec)
    events=parse_bsk_events(spec)
    assert len(events)==1 and events[0].start_s==2.
    assert events[0].parameters['remaining_ratio']==pytest.approx(1.-magnitude)
    assert spec==before


@pytest.mark.parametrize('effect,parameters',[
    ('eps_battery_capacity_loss',{'remaining_capacity_ratio':.8}),
    ('solar_panel_efficiency_loss',{'remaining_efficiency_ratio':.8}),
    ('thermal_radiator_rejection_loss',{'remaining_rejection_ratio':.8}),
    ('eps_battery_capacity_loss',{'remaining_ratio':.8}),
    ('eps_battery_capacity_loss',{'loss_fraction':.2}),
    ('eps_battery_capacity_loss',{'loss_pct':20.}),
])
def test_explicit_dimensional_effect_parameters_keep_their_existing_priority(effect,parameters):
    category='faults' if effect=='eps_battery_capacity_loss' else 'degradations'
    spec={'events':{category:[{'id':'loss','effect':effect,
        'start_s':2.,'magnitude':.3,'parameters':parameters}]}}
    assert parse_bsk_events(spec)[0].parameters==parameters


def test_absent_magnitude_preserves_existing_effect_default_parameters():
    spec={'events':{'faults':[{'id':'loss','effect':'eps_battery_capacity_loss',
        'start_s':2.,'parameters':{}}]}}
    assert parse_bsk_events(spec)[0].parameters=={}


@pytest.mark.parametrize('magnitude',[-.1,1.1,True,float('nan'),float('inf'),'unknown'])
def test_invalid_resource_loss_scalar_is_rejected_instead_of_ignored(magnitude):
    spec={'events':{'faults':[{'id':'loss','effect':'eps_battery_capacity_loss',
        'start_s':2.,'magnitude':magnitude,'parameters':{}}]}}
    with pytest.raises(ValueError,match='LOSS_MAGNITUDE'):
        parse_bsk_events(spec)
