"""Audit frozen R84-R4 crossed-axis independent native evidence and source separation.

用途：核对 7560 个原生工况、种子、物理读回、两档测量来源和原始证据哈希。
参数：--evidence-dir、四个 --prior-case-file、--report。
输出：独立机器可读审计收据；不签发 R85 或飞行资格。
"""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path

from scripts.audit_r84r4_crossed_axis_native_pilot import ALLOWED, records, sha
from scripts.run_r84r4_crossed_independent import CONFIG, planned_cases, prior_seeds
from sat_sim.experiments.r84r3b_wheel_speed_measurement import (
    WheelSpeedMeasurementAssumptions, observe_wheel_speeds,
)


def audit(directory: Path, prior_paths: list[Path]) -> dict:
    config_bytes = CONFIG.read_bytes()
    config = json.loads(config_bytes)
    manifest = json.loads((directory / 'r84r4_crossed_independent_manifest.json').read_text(encoding='utf-8'))
    assert manifest['status'] == 'PASS'
    assert manifest['preregistration_sha256'] == hashlib.sha256(config_bytes).hexdigest()
    assert manifest['unique_native_cases'] == config['design']['unique_native_cases'] == 7560
    assert len(prior_paths) == 4
    assert all(sha(directory / name) == digest for name, digest in manifest['artifact_sha256'].items())
    prior = set().union(*(prior_seeds(path) for path in prior_paths))
    planned = list(planned_cases(config))
    profiles = {item['name']: WheelSpeedMeasurementAssumptions(**item['assumptions'])
                for item in config['wheel_measurement_profiles']}
    seen = set()
    cells = Counter()
    missing = Counter()
    keys = ('cases', 'imu', 'qa', 'observations_nominal', 'observations_stressed')
    streams = [records(directory / f'r84r4_crossed_independent_{key}.jsonl.gz') for key in keys]
    for index, (case, imu, qa, nominal, stressed) in enumerate(zip(*streams, strict=True)):
        plan = planned[index]
        rid = case['record_id']
        assert rid == f'r84r4_crossed_independent_{index + 1:05d}'
        assert all(item['record_id'] == rid for item in (imu, qa, nominal, stressed))
        seed = case['rng_seed']
        assert seed == plan['seed'] and seed not in seen and seed not in prior
        seen.add(seed)
        axis, wheel, initial, cohort, noise = (plan[key] for key in ('axis', 'wheel', 'initial', 'cohort', 'noise'))
        assert case['maneuver_axis_index'] == axis
        assert case['wheel_index_QA_ONLY'] == wheel
        assert case['initial_state_index'] == initial
        assert case['cohort_QA_ONLY'] == cohort
        assert case['capacity_mode_QA_ONLY'] == plan['capacity']
        assert case['gyro_noise_sigma_rad_s'] == noise
        assert case['repetition'] == plan['repetition']
        cells[(noise, axis, wheel, initial, cohort)] += 1
        receipts = case['native_receipts']
        refs = [item for item in receipts if item['event'] == 'FSW_REFERENCE_SET_OR_RESET']
        caps = [item for item in receipts if item['event'] == 'NATIVE_CPP_RW_CAPACITY_VERIFICATION']
        assert len(refs) == 8 and all(item['maneuver_axis_index'] == axis for item in refs)
        assert len(caps) == 1 and caps[0]['target_rw_index_QA_ONLY'] == wheel
        assert caps[0]['effector_cpp_readback_after_nm'] == {'healthy': .02, 'loss50': .01, 'loss75': .005}[plan['capacity']]
        assert len(imu['rows']) == len(qa['rows']) == 91
        for sensor, truth in zip(imu['rows'], qa['rows'], strict=True):
            assert sensor['time_s'] == truth['time_s']
            assert all(truth[f'fsw.native_reference_mrp_{other}'] == 0.0 for other in range(3) if other != axis)
        source = []
        for sensor, truth in zip(imu['rows'], qa['rows'], strict=True):
            row = dict(sensor)
            for channel in range(3):
                row[f'sensor.adcs.rw.speed_rad_s_{channel}'] = truth[f'rw.native_speed_rad_s_{channel}']
                row[f'command.adcs.rw.motor_torque_nm_{channel}'] = truth[f'rw.native_allocator_request_nm_{channel}']
            source.append(row)
        for name, observation, stride in (('nominal', nominal, 2), ('stressed', stressed, 3)):
            assert len(observation['rows']) == (46 if stride == 2 else 31)
            replay = observe_wheel_speeds(source, profiles[name], seed=seed * 2 + (name == 'stressed'))
            for sample_index, row in enumerate(observation['rows']):
                sensor = imu['rows'][sample_index * stride]
                truth = qa['rows'][sample_index * stride]
                assert set(row) == ALLOWED
                expected = {key: value for key, value in source[sample_index * stride].items()
                            if not key.startswith('sensor.adcs.rw.speed_rad_s_')}
                expected.update(replay[sample_index])
                assert row == expected
                assert row['source_time_s'] == sensor['time_s'] == truth['time_s']
                assert all(row[f'imu.raw_gyro_rad_s_{a}'] == sensor[f'imu.raw_gyro_rad_s_{a}'] for a in 'xyz')
                for channel in range(3):
                    assert row[f'command.adcs.rw.motor_torque_nm_{channel}'] == truth[f'rw.native_allocator_request_nm_{channel}']
                    valid = row[f'wheel_{channel}_valid']
                    assert valid == (row[f'sensor.adcs.rw.speed_rad_s_{channel}'] is not None)
                    missing[name] += not valid
    assert len(seen) == 7560
    assert len(cells) == 3 * 3 * 3 * 3 * 4
    assert all(count == (30 if noise == config['design']['primary_imu_sigma_rad_s'] else 20)
               for (noise, *_), count in cells.items())
    return {'status': 'PASS', 'qualification': config['qualification'],
            'unique_native_cases': len(seen), 'native_samples': len(seen) * 91,
            'paired_observation_samples': {'nominal': len(seen) * 46, 'stressed': len(seen) * 31},
            'prior_seed_overlap': 0, 'strata': len(cells),
            'missing_wheel_values': dict(missing), 'source_and_qa_isolation': True,
            'preregistration_sha256': manifest['preregistration_sha256'],
            'artifact_sha256': manifest['artifact_sha256'],
            'formal_dataset_eligible': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--prior-case-file', type=Path, action='append', required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.evidence_dir, args.prior_case_file)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in result.items() if key != 'artifact_sha256'}, indent=2))


if __name__ == '__main__':
    main()
