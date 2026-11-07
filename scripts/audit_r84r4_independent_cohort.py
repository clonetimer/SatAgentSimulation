"""Independently audit R84-R4 raw native cohort and analysis counts.

用途：核验压缩原始文件哈希、互斥种子、原生消息来源、测量回放及 QA 隔离。
参数：--evidence-dir、--prior-r2-cases、--pilot-cases、--analysis、--report。
输出：机器可读 PASS/FAIL 审计收据；不另选特征或阈值。
"""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path

from sat_sim.experiments.r84r3b_wheel_speed_measurement import (
    WheelSpeedMeasurementAssumptions, observe_wheel_speeds,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'configs/research/r84r4_independent_cohort_prereg_v1.json'
ALLOWED = {'time_s', 'source_time_s', *(f'imu.raw_gyro_rad_s_{axis}' for axis in 'xyz'),
           *(f'sensor.adcs.rw.speed_rad_s_{wheel}' for wheel in range(3)),
           *(f'command.adcs.rw.motor_torque_nm_{wheel}' for wheel in range(3)),
           *(f'wheel_{wheel}_valid' for wheel in range(3)),
           *(f'wheel_{wheel}_receipt_time_s' for wheel in range(3))}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def rows(path: Path):
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def audit(directory: Path, r2_path: Path, pilot_path: Path, analysis_path: Path) -> dict:
    config = json.loads(CONFIG.read_text(encoding='utf-8'))
    manifest = json.loads((directory / 'r84r4_independent_manifest.json').read_text(encoding='utf-8'))
    assert manifest['status'] == 'PASS'
    assert manifest['preregistration_sha256'] == sha(CONFIG)
    assert manifest['unique_native_cases'] == config['design']['unique_native_cases'] == 2160
    assert all(sha(directory / name) == digest for name, digest in manifest['artifact_sha256'].items())
    prior = {case['rng_seed'] for case in rows(r2_path)} | {case['rng_seed'] for case in rows(pilot_path)}
    params = WheelSpeedMeasurementAssumptions(**{key: value for key, value in config['wheel_measurement'].items() if key != 'basis'})
    keys = ('cases', 'observations', 'imu', 'qa')
    files = [rows(directory / f'r84r4_independent_{key}.jsonl.gz') for key in keys]
    seeds = set()
    counts = Counter()
    missing = 0
    for index, (case, observed, imu, qa) in enumerate(zip(*files, strict=True)):
        rid = f'r84r4_cohort_{index + 1:04d}'
        assert case['record_id'] == observed['record_id'] == imu['record_id'] == qa['record_id'] == rid
        assert len(observed['rows']) == 46 and len(imu['rows']) == len(qa['rows']) == 91
        context = (case['wheel_index_QA_ONLY'] * 3 + case['initial_state_index']) * 2 + (case['reference_mode'] == 'pulse09x4')
        cohort_index = config['design']['cohorts'].index(case['cohort_QA_ONLY'])
        seed = 1800000000 + context * 100000 + cohort_index * 10000 + case['repetition']
        assert case['context_index'] == context and case['rng_seed'] == seed
        assert seed not in prior and seed not in seeds
        seeds.add(seed)
        counts[(context, case['cohort_QA_ONLY'])] += 1
        assert case['capacity_mode_QA_ONLY'] == ('healthy' if cohort_index <= 1 else case['cohort_QA_ONLY'].removesuffix('_test'))
        capacity_receipts = [receipt for receipt in case['native_receipts']
                             if receipt.get('event') == 'NATIVE_CPP_RW_CAPACITY_VERIFICATION']
        assert len(capacity_receipts) == 1 and capacity_receipts[0]['physical_change_not_feature_edit']
        source = []
        for sensor, truth in zip(imu['rows'], qa['rows'], strict=True):
            assert sensor['time_s'] == truth['time_s']
            row = dict(sensor)
            for wheel in range(3):
                row[f'sensor.adcs.rw.speed_rad_s_{wheel}'] = truth[f'rw.native_speed_rad_s_{wheel}']
                row[f'command.adcs.rw.motor_torque_nm_{wheel}'] = truth[f'rw.native_allocator_request_nm_{wheel}']
            source.append(row)
        measured = observe_wheel_speeds(source, params, seed=seed)
        for observation_index, (row, measurement) in enumerate(zip(observed['rows'], measured, strict=True)):
            native = source[observation_index * params.sample_every_n_native_points]
            expected = {key: value for key, value in native.items() if not key.startswith('sensor.adcs.rw.speed_rad_s_')}
            expected.update(measurement)
            assert set(row) == ALLOWED and row == expected
            missing += sum(not row[f'wheel_{wheel}_valid'] for wheel in range(3))
    assert len(seeds) == 2160 and len(counts) == 72 and set(counts.values()) == {30}
    analysis = json.loads(analysis_path.read_text(encoding='utf-8'))
    assert analysis['preregistration_sha256'] == manifest['preregistration_sha256']
    assert analysis['unique_native_cases'] == 2160
    assert all(analysis['overall'][cohort]['total'] == 540 for cohort in config['design']['cohorts'])
    assert all(counts['total'] == 30 and counts['positive'] + counts['negative'] + counts['abstain'] == 30
               for counts in analysis['per_context'])
    return {'status': 'PASS', 'qualification': config['qualification'],
            'unique_new_seeds': len(seeds), 'prior_seed_overlap': 0,
            'physical_context_cohort_cells': len(counts), 'cases_per_cell': 30,
            'native_rows': len(seeds) * 91, 'observation_rows': len(seeds) * 46,
            'missing_wheel_values': missing, 'source_and_measurement_exact_match': True,
            'observation_allowlist_exact': True, 'qa_truth_excluded_from_observation': True,
            'preregistration_sha256': manifest['preregistration_sha256'],
            'artifact_sha256': manifest['artifact_sha256'], 'formal_dataset_eligible': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--prior-r2-cases', type=Path, required=True)
    parser.add_argument('--pilot-cases', type=Path, required=True)
    parser.add_argument('--analysis', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.evidence_dir, args.prior_r2_cases, args.pilot_cases, args.analysis)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in result.items() if key != 'artifact_sha256'}, indent=2))


if __name__ == '__main__':
    main()
