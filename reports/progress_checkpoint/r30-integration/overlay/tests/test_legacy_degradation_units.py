"""Legacy percentage compatibility must remain explicit and cannot clamp values."""
import copy

import pytest
from sat_sim.capability_agent import CapabilityAgentRequest, _coerce_common_llm_aliases
from sat_sim.task_models import canonicalize_task_spec
from sat_sim.task_validator import validate_task_spec


def draft(value, *, field='magnitude', kind='degradation'):
    carrier = 'degradations' if kind == 'degradation' else 'faults'
    return {'schema_version':'0.1.0','task_id':'legacy_unit_case','task_type':'subsystem',
        'capability_id':'subsystem.adcs_unified_native.v1',
        'target':{'level':'subsystem','name':'adcs','mode':kind},
        'simulation':{'duration_s':12.,'sample_s':1.},
        carrier:[{'effect_id':'gyro_noise_increase','onset_time_s':0.,field:value}]}


def normalize(spec):
    return _coerce_common_llm_aliases(spec, CapabilityAgentRequest(
        request='capability_id=subsystem.adcs_unified_native.v1 mode=degradation effect=gyro_noise_increase',
        backend='openai_compatible'))


@pytest.mark.parametrize('raw,expected,converted',[(10.,.1,True),(100.,1.,True),(.5,.5,False),(1.,1.,False),(0.,0.,False),(-5.,-5.,False),(150.,150.,False)])
def test_only_legacy_percentage_interval_converts_with_audit_record(raw,expected,converted):
    spec=draft(raw)
    original=copy.deepcopy(spec)
    out=normalize(spec)
    assert spec==original
    assert out['modifiers']['degradations'][0]['severity']==pytest.approx(expected)
    records=out.get('metadata',{}).get('legacy_unit_conversions',[])
    assert bool(records) is converted
    if converted:
        assert records==[{'contract':'v0.1.degradation.magnitude.percent_above_one',
            'event_index':0,'source_field':'magnitude','source_value':raw,'source_unit':'percent',
            'target_field':'severity','target_unit':'ratio','target_value':expected,'clamped':False}]
    if expected < 0 or expected > 1:
        validation=validate_task_spec(out)
        assert not validation.ok
        assert any(issue.path=='$.events.degradations[0].magnitude'
                   and issue.code=='TASKSPEC_MODEL_INVALID' for issue in validation.errors)


def test_explicit_severity_is_never_reinterpreted_as_percent():
    out=normalize(draft(10.,field='severity'))
    assert out['modifiers']['degradations'][0]['severity']==10.
    assert not out.get('metadata',{}).get('legacy_unit_conversions')


def test_canonical_magnitude_is_never_reinterpreted_as_legacy_percent():
    canonical=canonicalize_task_spec(normalize(draft(.2)))
    canonical['events']['degradations'][0]['magnitude']=10.
    out=normalize(canonical)
    assert out['modifiers']['degradations'][0]['severity']==10.
    assert not out.get('metadata',{}).get('legacy_unit_conversions')


def test_declared_nonlegacy_unit_is_never_reinterpreted():
    spec=draft(10.)
    spec['degradations'][0]['unit']='fraction'
    out=normalize(spec)
    assert out['modifiers']['degradations'][0]['severity']==10.
    assert not out.get('metadata',{}).get('legacy_unit_conversions')


@pytest.mark.parametrize('previous',[7,'old value',{'note':'old value'}])
def test_untyped_previous_metadata_is_preserved_without_becoming_fake_history(previous):
    spec=draft(10.)
    spec['metadata']={'legacy_unit_conversions':copy.deepcopy(previous)}
    original=copy.deepcopy(spec)
    out=normalize(spec)
    records=out['metadata']['legacy_unit_conversions']
    assert spec==original
    assert records[0]=={'record_kind':'preserved_untyped_input','value':previous}
    assert records[1]['source_value']==10. and records[1]['target_value']==.1
    assert out['modifiers']['degradations'][0]['severity']==.1
