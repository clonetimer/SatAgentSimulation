"""Analyze a frozen three-axis R84-R4 independent native cohort.

用途：按已知三轴顺序和初始姿态健康校准，独立比较三段 IMU、轮速/命令与融合评分。
参数：--evidence-dir、--report。
输出：逐噪声、测量档、轴顺序、故障轮的计数与弃权；故障目标轮仅用于最终分组。
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path
from statistics import median

from scripts import run_r84r2_multipulse_multifeature_observability as r2
from scripts.analyze_r84r4_independent_cohort import gains, nearest_rank

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'configs/research/r84r4_triaxis_independent_prereg_v1.json'


def imu_blocks(rows: list[dict]) -> list[dict[str, float]]:
    if len(rows) != 271:
        raise RuntimeError('triaxis IMU sample count mismatch')
    blocks = []
    for block in range(3):
        section = [{**row, 'time_s': row['time_s'] - block * 18.0}
                   for row in rows[block * 90:block * 90 + 91]]
        blocks.append(r2.extract_multifeatures(section))
    return blocks


def wheel_blocks(rows: list[dict], stride: int) -> tuple[float, ...] | None:
    expected = 136 if stride == 2 else 91
    block_length = 45 if stride == 2 else 30
    if len(rows) != expected:
        raise RuntimeError('triaxis wheel observation sample count mismatch')
    result = []
    for block in range(3):
        section = [{**row, 'source_time_s': row['source_time_s'] - block * 18.0}
                   for row in rows[block * block_length:block * block_length + block_length + 1]]
        item = gains(section)
        if item is None:
            return None
        result.extend(item)
    return tuple(result)


def read(path: Path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def wheel_model(values: list[tuple[float, ...] | None], minimum: int):
    valid = [item for item in values if item is not None]
    if len(valid) < minimum:
        return None
    centers = [median(item[dimension] for item in valid) for dimension in range(9)]
    scales = [max(median(abs(item[dimension] - centers[dimension]) for item in valid), 0.01)
              for dimension in range(9)]
    return centers, scales


def wheel_score(item, model):
    centers, scales = model
    return max(abs(item[dimension] - centers[dimension]) / scales[dimension] for dimension in range(9))


def _counts():
    return {'total': 0, 'positive': 0, 'negative': 0, 'abstain': 0}


def evaluate(records: list[dict], config: dict) -> dict:
    groups = defaultdict(list)
    for item in records:
        # A priori mission information only: no target-wheel or severity in calibration key.
        groups[(item['noise'], item['order'], item['initial'])].append(item)
    overall = defaultdict(_counts)
    per_cell = defaultdict(_counts)
    calibration_receipts = []
    expected_groups = 3 * 3 * 3
    if len(groups) != expected_groups:
        raise RuntimeError('triaxis independent stratum count mismatch')
    for group_key, members in sorted(groups.items()):
        noise, order, initial = group_key
        cal = [item for item in members if item['cohort'] == 'healthy_calibration']
        expected = 90 if noise == config['design']['primary_imu_sigma_rad_s'] else 60
        if len(cal) != expected:
            raise RuntimeError(f'triaxis independent calibration count mismatch: {group_key}')
        imu_models = [r2._robust_parameters([item['imu_features'][block] for item in cal])
                      for block in range(3)]
        imu_scores = {item['id']: max(r2._composite_score(item['imu_features'][block], *imu_models[block])
                                      for block in range(3)) for item in members}
        imu_threshold = nearest_rank([imu_scores[item['id']] for item in cal], 0.90)
        for profile in config['wheel_measurement_profiles']:
            name = profile['name']
            wheel_cal = [item['wheel_gain'][name] for item in cal]
            minimum = math.ceil(expected * config['analysis']['minimum_valid_healthy_fraction'])
            w_model = wheel_model(wheel_cal, minimum)
            wheel_scores = ({item['id']: wheel_score(item['wheel_gain'][name], w_model)
                             for item in members if item['wheel_gain'][name] is not None}
                            if w_model is not None else {})
            wheel_threshold = nearest_rank([wheel_scores[item['id']] for item in cal
                                             if item['id'] in wheel_scores], 0.90) if w_model else None
            fusion_cal = [item for item in cal if item['id'] in wheel_scores]
            # Healthy normalization does not use fault labels or control outcomes.
            imu_normalizer = max(median(imu_scores[item['id']] for item in cal), 1e-9)
            wheel_normalizer = (max(median(wheel_scores[item['id']] for item in fusion_cal), 1e-9)
                                if fusion_cal else None)
            def fused(item):
                return max(imu_scores[item['id']] / imu_normalizer,
                           wheel_scores[item['id']] / wheel_normalizer)
            fused_threshold = nearest_rank([fused(item) for item in fusion_cal], 0.90) if w_model else None
            calibration_receipts.append({'noise_sigma_rad_s': noise, 'axis_order_index': order,
                                         'initial_state_index': initial, 'wheel_profile': name,
                                         'healthy_calibration_total': len(cal),
                                         'valid_wheel_calibration': len(fusion_cal),
                                         'imu_threshold': imu_threshold,
                                         'wheel_threshold': wheel_threshold,
                                         'fusion_threshold': fused_threshold})
            for item in members:
                outcomes = {'imu': imu_scores[item['id']] > imu_threshold,
                            'wheel_command': (wheel_scores[item['id']] > wheel_threshold
                                              if item['id'] in wheel_scores else None),
                            'fusion': (fused(item) > fused_threshold
                                       if item['id'] in wheel_scores and fused_threshold is not None else None)}
                for method, outcome in outcomes.items():
                    keys = ((noise, name, method, item['cohort']),
                            (noise, name, method, order, initial, item['wheel'], item['cohort']))
                    for store, key in ((overall, keys[0]), (per_cell, keys[1])):
                        counts = store[key]
                        counts['total'] += 1
                        counts['abstain' if outcome is None else 'positive' if outcome else 'negative'] += 1
    return {'status': 'ANALYZED',
            'calibration_uses_fault_target_wheel': False,
            'calibration_uses_known_axis_order': True,
            'overall': [{'noise_sigma_rad_s': key[0], 'wheel_profile': key[1],
                         'method': key[2], 'cohort': key[3], **counts}
                        for key, counts in sorted(overall.items())],
            'per_cell': [{'noise_sigma_rad_s': key[0], 'wheel_profile': key[1],
                          'method': key[2], 'axis_order_index': key[3],
                          'initial_state_index': key[4], 'fault_target_wheel_QA_ONLY': key[5],
                          'cohort': key[6], **counts}
                         for key, counts in sorted(per_cell.items())],
            'calibration_receipts': calibration_receipts}


def analyze(directory: Path) -> dict:
    config_bytes = CONFIG.read_bytes()
    config = json.loads(config_bytes)
    manifest = json.loads((directory/'r84r4_triaxis_independent_manifest.json').read_text(encoding='utf-8'))
    if (manifest['status'] != 'PASS' or
            manifest['preregistration_sha256'] != hashlib.sha256(config_bytes).hexdigest() or
            manifest['unique_native_cases'] != config['design']['unique_native_cases']):
        raise RuntimeError('triaxis independent source manifest mismatch')
    names = ('cases', 'imu', 'observations_nominal', 'observations_stressed')
    streams = [read(directory/f'r84r4_triaxis_independent_{name}.jsonl.gz') for name in names]
    records = []
    for case, imu, nominal, stressed in zip(*streams, strict=True):
        rid = case['record_id']
        if not rid == imu['record_id'] == nominal['record_id'] == stressed['record_id']:
            raise RuntimeError('triaxis independent record identity mismatch')
        records.append({'id': rid, 'noise': case['gyro_noise_sigma_rad_s'],
                        'order': case['axis_order_index'],
                        'initial': case['initial_state_index'],
                        'wheel': case['wheel_index_QA_ONLY'],
                        'cohort': case['cohort_QA_ONLY'],
                        'imu_features': imu_blocks(imu['rows']),
                        'wheel_gain': {'nominal': wheel_blocks(nominal['rows'], 2),
                                       'stressed': wheel_blocks(stressed['rows'], 3)}})
    if len(records) != manifest['unique_native_cases']:
        raise RuntimeError('triaxis independent raw row count mismatch')
    result = evaluate(records, config)
    result.update({'qualification': config['qualification'],
                   'preregistration_sha256': manifest['preregistration_sha256'],
                   'native_cases': len(records),
                   'formal_dataset_eligible': False})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.evidence_dir)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': result['status'], 'native_cases': result['native_cases'],
                      'overall': result['overall']}, indent=2))


if __name__ == '__main__':
    main()

