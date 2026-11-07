"""Crossed-axis native physics regression for the R84-R4 study design."""
from __future__ import annotations

import json

from sat_sim.experiments.native_imu_rw_crossed_axis_r84r4 import run_native_case


def test_fault_target_and_commanded_axis_are_independent():
    case = run_native_case(wheel=2, excitation_axis=0, initial=1,
                           reference_mode='pulse09x4', capacity_mode='loss75',
                           gyro_noise_std_rad_s=0.0001, seed=1900211017)
    assert case['wheel_index_QA_ONLY'] == 2
    assert case['maneuver_axis_index'] == 0
    receipts = case['native_event_receipts']
    fault = next(item for item in receipts if item['event'] == 'NATIVE_CPP_RW_CAPACITY_VERIFICATION')
    assert fault['target_rw_index_QA_ONLY'] == 2
    assert fault['effector_cpp_readback_after_nm'] == 0.005
    references = [item for item in receipts if item['event'] == 'FSW_REFERENCE_SET_OR_RESET']
    assert len(references) == 8
    assert all(item['maneuver_axis_index'] == 0 for item in references)
    assert all(item['native_mrp_readback'][1:] == [0.0, 0.0] for item in references)
    assert any(item['native_mrp_readback'][0] != 0.0 for item in references)
    assert len(case['sensor_rows']) == len(case['qa_rows']) == 91


def test_preregistered_matrix_has_81_disjoint_new_seeds():
    from scripts.run_r84r4_crossed_axis_native_pilot import CONFIG, cases
    config = json.loads(CONFIG.read_text(encoding='utf-8'))
    matrix = list(cases(config))
    assert len(matrix) == len({case['rng_seed'] for case in matrix}) == 81
    assert len({(case['maneuver_axis_index'], case['wheel_index_QA_ONLY'],
                 case['initial_state_index'], case['capacity_mode_QA_ONLY']) for case in matrix}) == 81
    assert all(1900000000 <= case['rng_seed'] < 2**31 for case in matrix)
