"""Transfer frozen R84-R2 IMU features to R84-R4's independent native seeds.

用途：在已见到 R4 主分析后，只读比较原 R2 十六特征方案的目标轮条件上界与目标轮盲评。
参数：--evidence-dir 原始 R4 证据，--profile local/cloud，--report 输出统计 JSON。
输出：逐物理上下文与汇总计数；后验方法比较，不授予新的预注册确认资格。
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path

from scripts import run_r84r2_multipulse_multifeature_observability as r2


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'configs/research/r84r4_imu_transfer_posthoc_v1.json'
R2_SOURCE = ROOT / 'scripts/run_r84r2_multipulse_multifeature_observability.py'


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def matches_text(path: Path, expected: str) -> bool:
    raw = path.read_bytes()
    lf = raw.replace(b'\r\n', b'\n')
    return any(hashlib.sha256(data).hexdigest() == expected
               for data in (raw, lf, lf.replace(b'\n', b'\r\n')))


def read(path: Path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def decompressed_sha(path: Path) -> str:
    h = hashlib.sha256()
    with gzip.open(path, 'rb') as stream:
        for data in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def evaluate(records: list[dict], *, target_blind: bool) -> dict:
    def group_key(item: dict) -> tuple:
        if target_blind:
            return item['initial'], item['reference']
        return item['wheel'], item['initial'], item['reference']

    groups = defaultdict(list)
    for record in records:
        groups[group_key(record)].append(record)
    per_group = []
    overall = defaultdict(lambda: {'total': 0, 'positive': 0, 'negative': 0, 'abstain': 0})
    by_reference = defaultdict(lambda: {'total': 0, 'positive': 0, 'negative': 0, 'abstain': 0})
    for key, members in sorted(groups.items()):
        calibration = [item['feature'] for item in members if item['cohort'] == 'healthy_calibration']
        expected = 90 if target_blind else 30
        if len(calibration) != expected:
            raise RuntimeError(f'IMU transfer calibration mismatch: {key} has {len(calibration)}')
        centers, scales = r2._robust_parameters(calibration)
        threshold = r2._nearest_rank([r2._composite_score(item, centers, scales) for item in calibration], 0.90)
        counts = defaultdict(lambda: {'total': 0, 'positive': 0, 'negative': 0, 'abstain': 0})
        for item in members:
            score = r2._composite_score(item['feature'], centers, scales)
            status = 'positive' if score > threshold else 'negative'
            for target in (counts[item['cohort']], overall[item['cohort']], by_reference[(item['reference'], item['cohort'])]):
                target['total'] += 1
                target[status] += 1
        per_group.append({'group': list(key), 'calibration_size': len(calibration),
                          'threshold': threshold, 'cohorts': dict(counts)})
    return {'target_wheel_used_to_choose_calibration': not target_blind,
            'maneuver_axis_used_to_choose_calibration': not target_blind,
            'axis_and_fault_target_confound': True,
            'overall': dict(overall),
            'by_reference': [{'reference_mode': reference, 'cohort': cohort, **counts}
                             for (reference, cohort), counts in sorted(by_reference.items())],
            'per_group': per_group}


def analyze(directory: Path, *, profile: str = 'local') -> dict:
    if profile not in ('local', 'cloud'):
        raise ValueError('IMU transfer profile must be local or cloud')
    config = json.loads(CONFIG.read_text(encoding='utf-8'))
    manifest = json.loads((directory / 'r84r4_independent_manifest.json').read_text(encoding='utf-8'))
    if (manifest['status'] != 'PASS'
            or manifest['preregistration_sha256'] != config['source_r4_preregistration_sha256']
            or not matches_text(R2_SOURCE, config['source_r2_script_sha256'])):
        raise RuntimeError('IMU transfer source or R4 run identity mismatch')
    actual_sha = {name: sha(directory / name) for name in config['input_sha256']}
    expected_sha = (config['input_sha256'] if profile == 'local' else
                    {name: manifest['artifact_sha256'][name] for name in config['input_sha256']})
    if actual_sha != expected_sha:
        raise RuntimeError('IMU transfer source data hash mismatch')
    records = []
    case_stream = read(directory / 'r84r4_independent_cases.jsonl.gz')
    imu_stream = read(directory / 'r84r4_independent_imu.jsonl.gz')
    for case, imu in zip(case_stream, imu_stream, strict=True):
        if case['record_id'] != imu['record_id']:
            raise RuntimeError('IMU transfer record mismatch')
        records.append({'wheel': case['wheel_index_QA_ONLY'],
                        'initial': case['initial_state_index'],
                        'reference': case['reference_mode'],
                        'cohort': case['cohort_QA_ONLY'],
                        'feature': r2.extract_multifeatures(imu['rows'])})
    if len(records) != manifest['unique_native_cases'] == 2160:
        raise RuntimeError('IMU transfer count mismatch')
    return {'status': 'EXPLORATORY_TRANSFER', 'qualification': config['qualification'],
            'source_r2_script_sha256': config['source_r2_script_sha256'],
            'source_r4_preregistration_sha256': config['source_r4_preregistration_sha256'],
            'source_profile': profile, 'source_input_sha256': actual_sha,
            'decompressed_input_sha256': {name: decompressed_sha(directory / name) for name in config['input_sha256']},
            'unique_native_cases': len(records),
            'conditional_upper_bound': evaluate(records, target_blind=False),
            'target_blind': evaluate(records, target_blind=True),
            'formal_dataset_eligible': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--profile', choices=('local', 'cloud'), default='local')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.evidence_dir, profile=args.profile)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: {'overall': value['overall'], 'by_reference': value['by_reference']}
                      for key, value in result.items() if key in ('conditional_upper_bound', 'target_blind')}, indent=2))


if __name__ == '__main__':
    main()
