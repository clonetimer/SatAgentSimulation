"""Audit R84-R4 crossed-axis native source separation and seed matrix.

用途：独立核验 81 个交叉工况的原生指令轴、物理故障轮、消息来源和互斥种子。
参数：--evidence-dir、--prior-r2-cases、--prior-r4-cases、--prior-r4-primary-cases、--report。
输出：机器可读收据；不计算诊断率或授予数据资格。
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path

ALLOWED = {'time_s', 'source_time_s', *(f'imu.raw_gyro_rad_s_{axis}' for axis in 'xyz'),
           *(f'sensor.adcs.rw.speed_rad_s_{wheel}' for wheel in range(3)),
           *(f'command.adcs.rw.motor_torque_nm_{wheel}' for wheel in range(3)),
           *(f'wheel_{wheel}_valid' for wheel in range(3)),
           *(f'wheel_{wheel}_receipt_time_s' for wheel in range(3))}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def records(path: Path):
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def audit(directory: Path, prior_r2: Path, prior_r4: Path, prior_r4_primary: Path) -> dict:
    manifest = json.loads((directory / 'r84r4_crossed_manifest.json').read_text(encoding='utf-8'))
    assert manifest['status'] == 'PASS' and manifest['unique_native_cases'] == 81
    assert all(sha(directory / name) == digest for name, digest in manifest['artifact_sha256'].items())
    previous = {case['rng_seed'] for case in records(prior_r2)}
    previous |= {case['rng_seed'] for case in records(prior_r4)}
    previous |= {case['rng_seed'] for case in records(prior_r4_primary)}
    seen = set()
    cells = set()
    healthy_physics = defaultdict(list)
    missing = 0
    streams = [records(directory / f'r84r4_crossed_{key}.jsonl.gz')
               for key in ('cases', 'observation', 'imu', 'qa')]
    for case, observed, imu, qa in zip(*streams, strict=True):
        rid = case['record_id']
        assert rid == observed['record_id'] == imu['record_id'] == qa['record_id']
        axis, wheel = case['maneuver_axis_index'], case['wheel_index_QA_ONLY']
        initial, capacity = case['initial_state_index'], case['capacity_mode_QA_ONLY']
        assert axis in range(3) and wheel in range(3) and initial in range(3)
        assert capacity in ('healthy', 'loss50', 'loss75')
        cell = axis, wheel, initial, capacity
        assert cell not in cells
        cells.add(cell)
        seed = 1900000000 + axis * 1000000 + wheel * 100000 + initial * 10000 + {'healthy': 0, 'loss50': 1, 'loss75': 2}[capacity] * 1000 + 17
        assert case['rng_seed'] == seed and seed not in previous and seed not in seen
        seen.add(seed)
        receipts = case['native_receipts']
        refs = [item for item in receipts if item['event'] == 'FSW_REFERENCE_SET_OR_RESET']
        caps = [item for item in receipts if item['event'] == 'NATIVE_CPP_RW_CAPACITY_VERIFICATION']
        assert len(refs) == 8 and all(item['maneuver_axis_index'] == axis for item in refs)
        assert len(caps) == 1 and caps[0]['target_rw_index_QA_ONLY'] == wheel
        assert caps[0]['effector_cpp_readback_after_nm'] == {'healthy': 0.02, 'loss50': 0.01, 'loss75': 0.005}[capacity]
        assert len(observed['rows']) == 46 and len(imu['rows']) == len(qa['rows']) == 91
        physical = []
        for sensor, truth in zip(imu['rows'], qa['rows'], strict=True):
            assert sensor['time_s'] == truth['time_s']
            assert all(truth[f'fsw.native_reference_mrp_{other}'] == 0.0 for other in range(3) if other != axis)
            physical.append({key: value for key, value in truth.items()
                             if key != 'rw.native_target_applied_torque_nm_QA_ONLY'})
        if capacity == 'healthy':
            healthy_physics[(axis, initial)].append(physical)
        for index, row in enumerate(observed['rows']):
            sensor = imu['rows'][index * 2]
            truth = qa['rows'][index * 2]
            assert set(row) == ALLOWED and row['time_s'] == row['source_time_s'] == sensor['time_s'] == truth['time_s']
            assert all(row[f'imu.raw_gyro_rad_s_{a}'] == sensor[f'imu.raw_gyro_rad_s_{a}'] for a in 'xyz')
            for candidate in range(3):
                assert row[f'command.adcs.rw.motor_torque_nm_{candidate}'] == truth[f'rw.native_allocator_request_nm_{candidate}']
                assert row[f'wheel_{candidate}_valid'] == (row[f'sensor.adcs.rw.speed_rad_s_{candidate}'] is not None)
                missing += not row[f'wheel_{candidate}_valid']
    assert len(cells) == len(seen) == 81
    assert len(healthy_physics) == 9
    assert all(len(items) == 3 and items[0] == items[1] == items[2]
               for items in healthy_physics.values())
    return {'status': 'PASS', 'qualification': manifest['qualification'],
            'unique_crossed_cases': len(cells), 'prior_seed_overlap': 0,
            'native_samples': 81 * 91, 'observation_samples': 81 * 46,
            'missing_wheel_values': missing,
            'reference_axis_and_cpp_target_readback_separate': True,
            'healthy_physics_equal_across_nominal_fault_wheels': True,
            'observation_qa_isolation': True,
            'preregistration_sha256': manifest['preregistration_sha256'],
            'artifact_sha256': manifest['artifact_sha256'], 'formal_dataset_eligible': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--prior-r2-cases', type=Path, required=True)
    parser.add_argument('--prior-r4-cases', type=Path, required=True)
    parser.add_argument('--prior-r4-primary-cases', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.evidence_dir, args.prior_r2_cases, args.prior_r4_cases,
                   args.prior_r4_primary_cases)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in result.items() if key != 'artifact_sha256'}, indent=2))


if __name__ == '__main__':
    main()
