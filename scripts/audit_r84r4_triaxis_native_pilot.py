"""Audit the frozen R84-R4 three-axis native pilot without scoring faults.

用途：验证新种子、三轴实际参考、原生 C++ 故障权限、消息来源和测量回放。
参数：--evidence-dir、重复五次 --prior-case-file、--report。
输出：独立机器可读审计收据；不授予诊断或数据集资格。
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from scripts.audit_r84r4_crossed_axis_native_pilot import ALLOWED, records, sha
from scripts.run_r84r4_triaxis_native_pilot import CONFIG, cases, physics_without_target_torque
from sat_sim.experiments.r84r3b_wheel_speed_measurement import (
    WheelSpeedMeasurementAssumptions, observe_wheel_speeds,
)


def audit(directory: Path, prior_paths: list[Path]) -> dict:
    config_bytes = CONFIG.read_bytes()
    config = json.loads(config_bytes)
    manifest = json.loads((directory/'r84r4_triaxis_manifest.json').read_text(encoding='utf-8'))
    assert manifest['status'] == 'PASS' and manifest['unique_native_cases'] == 81
    assert manifest['preregistration_sha256'] == hashlib.sha256(config_bytes).hexdigest()
    assert all(sha(directory/name) == digest for name, digest in manifest['artifact_sha256'].items())
    assert len(prior_paths) == 5
    old = {case['rng_seed'] for path in prior_paths for case in records(path)}
    params_dict = json.loads((Path(__file__).resolve().parents[1]/'configs/research/r84r4_independent_cohort_prereg_v1.json').read_text())['wheel_measurement']
    params = WheelSpeedMeasurementAssumptions(**{key: value for key, value in params_dict.items() if key != 'basis'})
    plan = list(cases(config))
    seen = set()
    physical_healthy = defaultdict(list)
    missing = 0
    streams = [records(directory/f'r84r4_triaxis_{key}.jsonl.gz')
               for key in ('cases', 'imu', 'qa', 'observation')]
    for index, (case, imu, qa, observation) in enumerate(zip(*streams, strict=True)):
        expected = plan[index]
        rid = f'r84r4_triaxis_{index + 1:03d}'
        assert all(item['record_id'] == rid for item in (case, imu, qa, observation))
        assert all(case[key] == value for key, value in expected.items())
        seed = case['rng_seed']
        assert seed not in seen and seed not in old
        seen.add(seed)
        order = config['design']['axis_orders'][case['axis_order_index']]
        receipts = case['native_receipts']
        refs = [item for item in receipts if item['event'] == 'FSW_REFERENCE_SET_OR_RESET']
        caps = [item for item in receipts if item['event'] == 'NATIVE_CPP_RW_CAPACITY_VERIFICATION']
        assert len(refs) == 24 and len(caps) == 1
        assert [item['maneuver_axis_index'] for item in refs] == [axis for axis in order for _ in range(8)]
        assert [item['axis_order_position'] for item in refs] == [position for position in range(3) for _ in range(8)]
        assert caps[0]['target_rw_index_QA_ONLY'] == case['wheel_index_QA_ONLY']
        assert caps[0]['effector_cpp_readback_after_nm'] == {'healthy': .02, 'loss50': .01, 'loss75': .005}[case['capacity_mode_QA_ONLY']]
        assert len(imu['rows']) == len(qa['rows']) == 271
        assert len(observation['rows']) == 136
        active = Counter()
        source = []
        for sensor, truth in zip(imu['rows'], qa['rows'], strict=True):
            assert sensor['time_s'] == truth['time_s']
            nonzero = [axis for axis in range(3) if abs(truth[f'fsw.native_reference_mrp_{axis}']) > 1e-15]
            assert len(nonzero) <= 1
            if nonzero:
                active[nonzero[0]] += 1
            row = dict(sensor)
            for wheel in range(3):
                row[f'sensor.adcs.rw.speed_rad_s_{wheel}'] = truth[f'rw.native_speed_rad_s_{wheel}']
                row[f'command.adcs.rw.motor_torque_nm_{wheel}'] = truth[f'rw.native_allocator_request_nm_{wheel}']
            source.append(row)
        assert set(active) == {0, 1, 2}
        if case['capacity_mode_QA_ONLY'] == 'healthy':
            physical_healthy[(case['axis_order_index'], case['initial_state_index'])].append(physics_without_target_torque(qa['rows']))
        replay = observe_wheel_speeds(source, params, seed=seed)
        for sample_index, observed in enumerate(observation['rows']):
            original = source[sample_index * params.sample_every_n_native_points]
            expected_row = {key: value for key, value in original.items()
                            if not key.startswith('sensor.adcs.rw.speed_rad_s_')}
            expected_row.update(replay[sample_index])
            assert observed == expected_row and set(observed) == ALLOWED
            missing += sum(not observed[f'wheel_{wheel}_valid'] for wheel in range(3))
    assert len(seen) == 81 and len(physical_healthy) == 9
    assert all(len(items) == 3 and items[0] == items[1] == items[2]
               for items in physical_healthy.values())
    return {'status': 'PASS', 'qualification': config['qualification'],
            'unique_native_cases': len(seen), 'native_samples': len(seen) * 271,
            'observation_samples': len(seen) * 136, 'reference_events': len(seen) * 24,
            'prior_seed_overlap': 0, 'missing_wheel_values': missing,
            'all_three_axes_observed': True, 'target_cpp_readback_verified': True,
            'healthy_physics_equal_across_nominal_target_wheels': True,
            'measurement_replay_and_qa_isolation': True,
            'preregistration_sha256': manifest['preregistration_sha256'],
            'artifact_sha256': manifest['artifact_sha256'], 'formal_dataset_eligible': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--prior-case-file', type=Path, required=True, action='append')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.evidence_dir, args.prior_case_file)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in result.items() if key != 'artifact_sha256'}, indent=2))


if __name__ == '__main__':
    main()
