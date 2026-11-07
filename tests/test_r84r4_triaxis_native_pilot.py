"""Frozen three-axis pilot matrix and native source contract."""
import json

import pytest

from scripts.run_r84r4_triaxis_native_pilot import (
    CONFIG, MEASUREMENT, SOURCE, cases, matches_frozen_text,
)
from sat_sim.experiments.native_imu_rw_triaxis_r84r4 import AXIS_ORDERS, run_native_case


def test_triaxis_preregistration_is_frozen():
    config = json.loads(CONFIG.read_text(encoding='utf-8'))
    plan = list(cases(config))
    assert config['status'] == 'FROZEN_BEFORE_EXECUTION'
    assert matches_frozen_text(SOURCE, config['native_source_sha256'])
    assert matches_frozen_text(MEASUREMENT, config['measurement_source_sha256'])
    assert tuple(map(tuple, config['design']['axis_orders'])) == AXIS_ORDERS
    assert len(plan) == len({item['rng_seed'] for item in plan}) == 81
    assert all(2030000000 <= item['rng_seed'] < 2**31 for item in plan)


def test_native_triaxis_events_and_fault_target_are_separate():
    result = run_native_case(wheel=2, axis_order_index=1, initial=0,
                             reference_mode='triaxis_pulse09x4', capacity_mode='loss75',
                             gyro_noise_std_rad_s=0.0001, seed=2030101001)
    assert len(result['sensor_rows']) == len(result['qa_rows']) == 271
    refs = [item for item in result['native_event_receipts']
            if item['event'] == 'FSW_REFERENCE_SET_OR_RESET']
    cap = next(item for item in result['native_event_receipts']
               if item['event'] == 'NATIVE_CPP_RW_CAPACITY_VERIFICATION')
    assert [item['maneuver_axis_index'] for item in refs] == [axis for axis in (1, 2, 0) for _ in range(8)]
    assert cap['target_rw_index_QA_ONLY'] == 2
    assert cap['effector_cpp_readback_after_nm'] == .005
    assert set(result['sensor_rows'][0]) == {'time_s', *(f'imu.raw_gyro_rad_s_{a}' for a in 'xyz')}


def test_native_triaxis_rejects_unknown_order():
    with pytest.raises(ValueError, match='UNREGISTERED_AXIS_ORDER'):
        run_native_case(wheel=0, axis_order_index=3, initial=0,
                        reference_mode='triaxis_pulse09x4', capacity_mode='healthy',
                        gyro_noise_std_rad_s=0.0001, seed=2030101002)
