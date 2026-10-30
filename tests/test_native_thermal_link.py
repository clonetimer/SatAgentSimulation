"""Actual native thermal-link wiring, compatibility, and invalid-input checks."""
from pathlib import Path
import sys

import pytest
from pydantic import ValidationError

SOURCE = Path(__file__).resolve().parents[1] / 'src'
sys.path.insert(0, str(SOURCE))
import sat_sim.bsk_engine.unified_native as native
from sat_sim.interactive.continuous_initial_config import ContinuousInitialConfiguration, initial_configuration_catalog

KEY = 'solar_panel_structure_conductance_w_k'


@pytest.fixture(autouse=True)
def isolated_source_is_loaded():
    assert Path(native.__file__).resolve().is_relative_to(SOURCE)


def config(**values):
    return native.UnifiedRuntimeConfig(
        capability_id=native.WHOLE_UNIFIED_CAPABILITY_ID,
        duration_s=30., step_s=.1, sample_s=.5, adcs_only=False,
        values={'environment_profile': 'spice_wmm', 'epoch_utc': '2026-01-01T00:00:00Z', **values}, events=())


def test_parameter_roundtrip_and_catalog_have_explicit_units():
    cfg = ContinuousInitialConfiguration.model_validate({'thermal': {KEY: 4.}})
    assert cfg.native_values() == {KEY: 4.}
    field = next(f for g in initial_configuration_catalog()['groups'] for f in g['fields'] if f['name'] == KEY)
    assert field['default'] == 2. and field['unit'] == 'W/K' and field['native_value_key'] == KEY


@pytest.mark.parametrize('value', [-1., 20.01, float('nan'), float('inf')])
def test_invalid_parameter_is_rejected_by_schema_and_actual_native_builder(value):
    with pytest.raises(ValidationError):
        ContinuousInitialConfiguration.model_validate({'thermal': {KEY: value}})
    with pytest.raises(ValueError, match='NATIVE_THERMAL_FIELD_OUT_OF_RANGE:' + KEY):
        native.UnifiedNativeRuntime(config(**{KEY: value})).run()


def test_link_changes_real_native_temperatures_and_preserves_default_design():
    original = native.UnifiedNativeRuntime(config()).run()
    explicit_original = native.UnifiedNativeRuntime(config(**{KEY: 2.})).run()
    candidate = native.UnifiedNativeRuntime(config(**{KEY: 4.})).run()
    temps = [k for k in original.trace_rows[0] if k.startswith('thermal.') and k.endswith('_temp_k')]
    assert [[r[k] for k in temps] for r in original.trace_rows] == [[r[k] for k in temps] for r in explicit_original.trace_rows]
    for result, expected in [(original, 2.), (explicit_original, 2.), (candidate, 4.)]:
        links = [p for p in result.summary['thermal_conduction_paths']
                 if {p['from_node'], p['to_node']} == {'solar_panel', 'structure'}]
        assert len(links) == 1 and links[0]['conductance_w_per_k'] == expected
        assert result.summary['runtime_message_read_error_count'] == 0
    # At the initial300K panel/285K body, stronger conductance cools the
    # panel and warms the body. No precomputed temperatures are substituted.
    assert candidate.trace_rows[-1]['thermal.solar_panel_temp_k'] < original.trace_rows[-1]['thermal.solar_panel_temp_k']
    assert candidate.trace_rows[-1]['thermal.structure_temp_k'] > original.trace_rows[-1]['thermal.structure_temp_k']
