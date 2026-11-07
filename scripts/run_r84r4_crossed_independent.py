"""Run frozen crossed-axis R84-R4 independent multinoise native campaign.

用途：用全新互斥种子执行 7560 个 Basilisk 工况，分别记录 IMU、两档轮速观察与物理 QA。
参数：--output-dir、--prior-case-file（可重复，必须给出 R2/R4 三批来源）。
输出：压缩原始 JSONL、预注册和来源哈希；不计算故障效果。
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import time

from scripts.run_r84r4_independent_cohort import matches_frozen_text, sha256
from scripts.run_r84r4_native_multichannel_feasibility import OUTPUT_KEYS
from sat_sim.experiments.native_imu_rw_crossed_axis_r84r4 import run_native_case
from sat_sim.experiments.r84r3b_wheel_speed_measurement import (
    WheelSpeedMeasurementAssumptions, observe_wheel_speeds,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT/'configs/research/r84r4_crossed_independent_prereg_v1.json'
SOURCES = {
    'native_source_sha256': ROOT/'src/sat_sim/experiments/native_imu_rw_crossed_axis_r84r4.py',
    'measurement_source_sha256': ROOT/'src/sat_sim/experiments/r84r3b_wheel_speed_measurement.py',
    'imu_feature_source_sha256': ROOT/'scripts/run_r84r2_multipulse_multifeature_observability.py',
    'wheel_stat_source_sha256': ROOT/'scripts/analyze_r84r4_independent_cohort.py',
    'analysis_source_sha256': ROOT/'scripts/analyze_r84r4_crossed_independent.py',
}


def prior_seeds(path: Path) -> set[int]:
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8') as stream:
        return {json.loads(line)['rng_seed'] for line in stream if line.strip()}


def planned_cases(config: dict):
    d = config['design']
    for noise_index, noise in enumerate(d['imu_noise_sigmas_rad_s']):
        repetitions = (d['primary_repetitions_per_cell'] if noise == d['primary_imu_sigma_rad_s']
                       else d['sensitivity_repetitions_per_cell'])
        for axis in d['maneuver_axes']:
            for wheel in d['fault_target_wheels']:
                for initial in d['initial_states']:
                    for cohort_index, cohort in enumerate(d['cohorts']):
                        capacity = 'healthy' if cohort.startswith('healthy') else cohort.removesuffix('_test')
                        for repetition in range(repetitions):
                            seed = (2000000000 + noise_index * 10000000 + axis * 1000000
                                    + wheel * 100000 + initial * 10000 + cohort_index * 1000 + repetition)
                            yield {'noise': noise, 'axis': axis, 'wheel': wheel, 'initial': initial,
                                   'cohort': cohort, 'capacity': capacity,
                                   'repetition': repetition, 'seed': seed}


def observation_rows(native: dict, params: WheelSpeedMeasurementAssumptions, seed: int) -> list[dict]:
    source = []
    for sensor, truth in zip(native['sensor_rows'], native['qa_rows'], strict=True):
        if sensor['time_s'] != truth['time_s']:
            raise RuntimeError('crossed independent source clock mismatch')
        row = dict(sensor)
        for wheel in range(3):
            row[f'sensor.adcs.rw.speed_rad_s_{wheel}'] = truth[f'rw.native_speed_rad_s_{wheel}']
            row[f'command.adcs.rw.motor_torque_nm_{wheel}'] = truth[f'rw.native_allocator_request_nm_{wheel}']
        source.append(row)
    measured = observe_wheel_speeds(source, params, seed=seed)
    result = []
    for index, values in enumerate(measured):
        original = source[index * params.sample_every_n_native_points]
        row = {key: value for key, value in original.items()
               if not key.startswith('sensor.adcs.rw.speed_rad_s_')}
        row.update(values)
        if set(row) != OUTPUT_KEYS:
            raise RuntimeError('crossed independent observation allowlist mismatch')
        result.append(row)
    return result


def run(output_dir: Path, prior_paths: list[Path]) -> dict:
    config_bytes = CONFIG.read_bytes()
    config = json.loads(config_bytes)
    if config['status'] != 'FROZEN_BEFORE_EXECUTION':
        raise RuntimeError('crossed independent preregistration status changed')
    if any(not matches_frozen_text(path, config[key]) for key, path in SOURCES.items()):
        raise RuntimeError('crossed independent frozen source changed')
    plan = list(planned_cases(config))
    seeds = [case['seed'] for case in plan]
    if len(plan) != config['design']['unique_native_cases'] or len(set(seeds)) != len(plan):
        raise RuntimeError('crossed independent plan or seeds invalid')
    if min(seeds) < 2000000000 or max(seeds) >= 2**31:
        raise RuntimeError('crossed independent seed range invalid')
    if len(prior_paths) != 4:
        raise RuntimeError('four prior case manifests required')
    previous = set()
    for path in prior_paths:
        previous |= prior_seeds(path)
    if set(seeds) & previous:
        raise RuntimeError('crossed independent seed overlaps prior archive')
    profiles = {item['name']: WheelSpeedMeasurementAssumptions(**item['assumptions'])
                for item in config['wheel_measurement_profiles']}
    if set(profiles) != {'nominal', 'stressed'}:
        raise RuntimeError('crossed independent wheel profiles invalid')
    output_dir.mkdir(parents=True, exist_ok=False)
    keys = ('cases', 'imu', 'qa', 'observations_nominal', 'observations_stressed')
    paths = {key: output_dir/f'r84r4_crossed_independent_{key}.jsonl.gz' for key in keys}
    streams = {key: gzip.open(path, 'wt', encoding='utf-8', newline='\n', compresslevel=6)
               for key, path in paths.items()}
    complete = 0
    start = time.monotonic()
    try:
        for case in plan:
            native = run_native_case(wheel=case['wheel'], excitation_axis=case['axis'],
                                     initial=case['initial'], reference_mode='pulse09x4',
                                     capacity_mode=case['capacity'],
                                     gyro_noise_std_rad_s=case['noise'], seed=case['seed'])
            observed = {name: observation_rows(native, params, case['seed'] * 2 + offset)
                        for offset, (name, params) in enumerate(profiles.items())}
            rid = f'r84r4_crossed_independent_{complete + 1:05d}'
            payloads = {
                'cases': {'record_id': rid, 'rng_seed': case['seed'],
                          'maneuver_axis_index': case['axis'],
                          'wheel_index_QA_ONLY': case['wheel'],
                          'initial_state_index': case['initial'],
                          'capacity_mode_QA_ONLY': case['capacity'],
                          'cohort_QA_ONLY': case['cohort'],
                          'gyro_noise_sigma_rad_s': case['noise'],
                          'repetition': case['repetition'],
                          'native_receipts': native['native_event_receipts']},
                'imu': {'record_id': rid, 'rows': native['sensor_rows']},
                'qa': {'record_id': rid, 'rows': native['qa_rows']},
                'observations_nominal': {'record_id': rid, 'rows': observed['nominal']},
                'observations_stressed': {'record_id': rid, 'rows': observed['stressed']},
            }
            for key, payload in payloads.items():
                streams[key].write(json.dumps(payload, sort_keys=True, allow_nan=False) + '\n')
            complete += 1
            if complete % 250 == 0:
                print(f'R84R4_CROSSED_NATIVE {complete}/{len(plan)} elapsed_s={time.monotonic() - start:.1f}', flush=True)
    finally:
        for stream in streams.values():
            stream.close()
    manifest = {'status': 'PASS', 'qualification': config['qualification'],
                'preregistration_sha256': hashlib.sha256(config_bytes).hexdigest(),
                'native_distribution': config['native_distribution'],
                'unique_native_cases': complete,
                'expected_cases': len(plan), 'prior_seed_overlap': 0,
                'artifact_sha256': {path.name: sha256(path) for path in paths.values()},
                'elapsed_seconds': round(time.monotonic() - start, 2),
                'formal_dataset_eligible': False}
    (output_dir/'r84r4_crossed_independent_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n',
                                                                        encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--prior-case-file', type=Path, required=True, action='append')
    args = parser.parse_args()
    print(json.dumps(run(args.output_dir, args.prior_case_file), indent=2))


if __name__ == '__main__':
    main()

