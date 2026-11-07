"""Run the frozen R84-R4 commanded-axis by fault-wheel native pilot.

用途：以互斥新种子执行 3×3×3×3 真 Basilisk 交叉因素工况，核验指令轴与故障轮已解耦。
参数：--output-dir 全新证据目录。
输出：分离的 IMU、观测、物理 QA、工况清单及 SHA-256；不计算诊断性能。
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path

from scripts.run_r84r4_independent_cohort import matches_frozen_text, sha256
from scripts.run_r84r4_native_multichannel_feasibility import output_rows
from sat_sim.experiments.native_imu_rw_crossed_axis_r84r4 import run_native_case
from sat_sim.experiments.r84r3b_wheel_speed_measurement import WheelSpeedMeasurementAssumptions


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'configs/research/r84r4_crossed_axis_native_pilot_prereg_v1.json'
SOURCE = ROOT / 'src/sat_sim/experiments/native_imu_rw_crossed_axis_r84r4.py'
MEASUREMENT = ROOT / 'src/sat_sim/experiments/r84r3b_wheel_speed_measurement.py'
BASE_MEASUREMENT = ROOT / 'configs/research/r84r4_independent_cohort_prereg_v1.json'


def cases(config: dict):
    d = config['design']
    for axis in d['maneuver_axes']:
        for wheel in d['fault_target_wheels']:
            for initial in d['initial_states']:
                for cap_index, capacity in enumerate(d['capacity_modes']):
                    seed = 1900000000 + axis * 1000000 + wheel * 100000 + initial * 10000 + cap_index * 1000 + 17
                    yield {'maneuver_axis_index': axis, 'wheel_index_QA_ONLY': wheel,
                           'initial_state_index': initial, 'capacity_mode_QA_ONLY': capacity,
                           'rng_seed': seed}


def physics_without_target_torque(rows: list[dict]) -> list[dict]:
    return [{key: value for key, value in row.items()
             if key != 'rw.native_target_applied_torque_nm_QA_ONLY'} for row in rows]


def run(output_dir: Path) -> dict:
    config_bytes = CONFIG.read_bytes()
    config = json.loads(config_bytes)
    if config['status'] != 'FROZEN_BEFORE_EXECUTION':
        raise RuntimeError('crossed-axis pilot config not frozen')
    if (not matches_frozen_text(SOURCE, config['source_module_sha256'])
            or not matches_frozen_text(MEASUREMENT, config['measurement_module_sha256'])):
        raise RuntimeError('crossed-axis source changed after freeze')
    params_dict = json.loads(BASE_MEASUREMENT.read_text(encoding='utf-8'))['wheel_measurement']
    params = WheelSpeedMeasurementAssumptions(**{key: value for key, value in params_dict.items() if key != 'basis'})
    plan = list(cases(config))
    if len(plan) != config['design']['unique_native_cases'] or len({case['rng_seed'] for case in plan}) != len(plan):
        raise RuntimeError('crossed-axis case matrix invalid')
    output_dir.mkdir(parents=True, exist_ok=False)
    paths = {key: output_dir / f'r84r4_crossed_{key}.jsonl.gz' for key in ('observation', 'imu', 'qa', 'cases')}
    streams = {key: gzip.open(path, 'wt', encoding='utf-8', newline='\n', compresslevel=6)
               for key, path in paths.items()}
    healthy_physics = defaultdict(list)
    replay_source = None
    completed = 0
    try:
        for case in plan:
            axis = case['maneuver_axis_index']
            wheel = case['wheel_index_QA_ONLY']
            capacity = case['capacity_mode_QA_ONLY']
            native = run_native_case(wheel=wheel, excitation_axis=axis,
                                     initial=case['initial_state_index'], reference_mode='pulse09x4',
                                     capacity_mode=capacity,
                                     gyro_noise_std_rad_s=config['design']['gyro_noise_sigma_rad_s'],
                                     seed=case['rng_seed'])
            reference_receipts = [item for item in native['native_event_receipts']
                                  if item['event'] == 'FSW_REFERENCE_SET_OR_RESET']
            fault_receipts = [item for item in native['native_event_receipts']
                              if item['event'] == 'NATIVE_CPP_RW_CAPACITY_VERIFICATION']
            assert len(reference_receipts) == 8 and len(fault_receipts) == 1
            assert all(item['maneuver_axis_index'] == axis for item in reference_receipts)
            assert fault_receipts[0]['target_rw_index_QA_ONLY'] == wheel
            assert fault_receipts[0]['cap_after_nm'] == {'healthy': 0.02, 'loss50': 0.01, 'loss75': 0.005}[capacity]
            for row in native['qa_rows']:
                assert all(row[f'fsw.native_reference_mrp_{other}'] == 0.0 for other in range(3) if other != axis)
            if capacity == 'healthy':
                healthy_physics[(axis, case['initial_state_index'])].append(physics_without_target_torque(native['qa_rows']))
            observed = output_rows(native, params)
            rid = f'r84r4_crossed_{completed + 1:03d}'
            payloads = {'observation': {'record_id': rid, 'rows': observed},
                        'imu': {'record_id': rid, 'rows': native['sensor_rows']},
                        'qa': {'record_id': rid, 'rows': native['qa_rows']},
                        'cases': {'record_id': rid, 'reference_mode': 'pulse09x4', **case,
                                  'native_receipts': native['native_event_receipts']}}
            for key, payload in payloads.items():
                streams[key].write(json.dumps(payload, sort_keys=True, allow_nan=False) + '\n')
            if (axis, wheel, case['initial_state_index'], capacity) == (2, 2, 2, 'loss75'):
                replay_source = (native, observed)
            completed += 1
        assert len(healthy_physics) == 9
        assert all(len(items) == 3 and items[0] == items[1] == items[2]
                   for items in healthy_physics.values())
        assert replay_source is not None
        original, observed = replay_source
        replay = run_native_case(wheel=2, excitation_axis=2, initial=2, reference_mode='pulse09x4',
                                 capacity_mode='loss75', gyro_noise_std_rad_s=config['design']['gyro_noise_sigma_rad_s'],
                                 seed=original['rng_seed'])
        assert replay['sensor_rows'] == original['sensor_rows'] and replay['qa_rows'] == original['qa_rows']
        assert output_rows(replay, params) == observed
    finally:
        for stream in streams.values():
            stream.close()
    manifest = {'status': 'PASS', 'qualification': config['qualification'],
                'preregistration_sha256': hashlib.sha256(config_bytes).hexdigest(),
                'unique_native_cases': completed, 'exact_replay': True,
                'healthy_physics_equal_across_nominal_fault_wheels': True,
                'maneuver_axis_and_fault_target_independent': True,
                'native_distribution': config['native_distribution'],
                'artifact_sha256': {path.name: sha256(path) for path in paths.values()},
                'formal_dataset_eligible': False}
    (output_dir / 'r84r4_crossed_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output_dir), indent=2))


if __name__ == '__main__':
    main()
