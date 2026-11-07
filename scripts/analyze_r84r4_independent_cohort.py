"""Analyze the preregistered R84-R4 independent native cohort.

用途：只从模型观测计算预定三轮命令-响应偏差，健康校准、健康控制和故障组严格隔离。
参数：--evidence-dir 原始证据目录、--report 输出 JSON 路径。
输出：逐组阳性率、弃权数和每工况计数；轮目标、故障真值和物理 QA 不参与特征或阈值。
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


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'configs/research/r84r4_independent_cohort_prereg_v1.json'


def read_gzip_jsonl(path: Path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def gains(rows: list[dict]) -> tuple[float, float, float] | None:
    result = []
    for wheel in range(3):
        speed_key = f'sensor.adcs.rw.speed_rad_s_{wheel}'
        command_key = f'command.adcs.rw.motor_torque_nm_{wheel}'
        motion = effort = 0.0
        pairs = 0
        for previous, current in zip(rows, rows[1:]):
            t = current['source_time_s']
            if not 2.8 <= t <= 17.6 or not previous[f'wheel_{wheel}_valid'] or not current[f'wheel_{wheel}_valid']:
                continue
            dt = t - previous['source_time_s']
            command = previous[command_key]
            speed0, speed1 = previous[speed_key], current[speed_key]
            if not (math.isfinite(dt) and dt > 0 and math.isfinite(command)
                    and math.isfinite(speed0) and math.isfinite(speed1)):
                return None
            motion += abs(speed1 - speed0)
            effort += abs(command) * dt
            pairs += 1
        if pairs < 10 or effort < 0.001:
            return None
        value = math.log(motion / effort + 1e-6)
        if not math.isfinite(value):
            return None
        result.append(value)
    return tuple(result)


def nearest_rank(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError('empty calibration')
    return sorted(values)[math.ceil(probability * len(values)) - 1]


def analyze(directory: Path) -> dict:
    config_bytes = CONFIG.read_bytes()
    config = json.loads(config_bytes)
    manifest = json.loads((directory / 'r84r4_independent_manifest.json').read_text(encoding='utf-8'))
    if manifest['status'] != 'PASS' or manifest['preregistration_sha256'] != hashlib.sha256(config_bytes).hexdigest():
        raise RuntimeError('missing, failed, or mismatched preregistered run')
    if manifest['unique_native_cases'] != config['design']['unique_native_cases']:
        raise RuntimeError('incomplete native cohort')
    case_path = directory / 'r84r4_independent_cases.jsonl.gz'
    observation_path = directory / 'r84r4_independent_observations.jsonl.gz'
    records = []
    observations = read_gzip_jsonl(observation_path)
    cases = read_gzip_jsonl(case_path)
    for index, (case, observation) in enumerate(zip(cases, observations, strict=True)):
        if case['record_id'] != observation['record_id']:
            raise RuntimeError(f'record identity mismatch at {index}')
        group = (case['initial_state_index'], case['reference_mode'])
        records.append({'id': case['record_id'], 'group': group, 'context': case['context_index'],
                        'cohort': case['cohort_QA_ONLY'], 'gains': gains(observation['rows'])})
    if len(records) != manifest['unique_native_cases']:
        raise RuntimeError('observation or case count incomplete')
    calibration = defaultdict(list)
    for record in records:
        if record['cohort'] == 'healthy_calibration' and record['gains'] is not None:
            calibration[record['group']].append(record['gains'])
    models = {}
    for group in [(initial, reference) for initial in range(3) for reference in ('zero', 'pulse09x4')]:
        values = calibration[group]
        if len(values) < 70:
            models[group] = None
            continue
        centers = [median(item[wheel] for item in values) for wheel in range(3)]
        scales = [max(median(abs(item[wheel] - centers[wheel]) for item in values), 0.01)
                  for wheel in range(3)]
        scores = [max(abs(item[wheel] - centers[wheel]) / scales[wheel] for wheel in range(3))
                  for item in values]
        models[group] = (centers, scales, nearest_rank(scores, 0.9))
    summary = defaultdict(lambda: {'total': 0, 'positive': 0, 'negative': 0, 'abstain': 0})
    for record in records:
        model = models[record['group']]
        key = (record['context'], record['cohort'])
        counts = summary[key]
        counts['total'] += 1
        if model is None or record['gains'] is None:
            counts['abstain'] += 1
            continue
        centers, scales, threshold = model
        score = max(abs(record['gains'][wheel] - centers[wheel]) / scales[wheel]
                    for wheel in range(3))
        counts['positive' if score > threshold else 'negative'] += 1
    contexts = []
    overall = defaultdict(lambda: {'total': 0, 'positive': 0, 'negative': 0, 'abstain': 0})
    for (context, cohort), counts in sorted(summary.items()):
        contexts.append({'context_index': context, 'cohort': cohort, **counts})
        for name, value in counts.items():
            overall[cohort][name] += value
    return {'status': 'ANALYZED', 'qualification': config['qualification'],
            'preregistration_sha256': manifest['preregistration_sha256'],
            'analysis_uses_qa_physics': False, 'target_wheel_used_for_scoring': False,
            'unique_native_cases': len(records),
            'calibration': [
                {'initial_state_index': group[0], 'reference_mode': group[1],
                 'valid_healthy_calibration': len(calibration[group]),
                 'threshold': models[group][2] if models[group] else None}
                for group in sorted(models)],
            'overall': dict(overall), 'per_context': contexts,
            'rate_denominator': 'all assigned independent seeds including abstentions',
            'formal_dataset_eligible': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.evidence_dir)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in result.items() if key != 'per_context'}, indent=2))


if __name__ == '__main__':
    main()
