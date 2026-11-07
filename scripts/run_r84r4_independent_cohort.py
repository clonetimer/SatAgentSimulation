"""Execute the frozen R84-R4 independent native cohort.

用途：按预注册的互斥种子运行 2160 个 Basilisk 工况，分离观测、原生 IMU、物理 QA 和标签。
参数：--output-dir 全新证据目录；--prior-r2-cases 与 --pilot-cases 用于执行前种子排重。
输出：压缩原始逐次 JSONL 和 SHA-256 清单；本脚本不计算或调整诊断阈值。
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import time

from run_r84r4_native_multichannel_feasibility import output_rows
from sat_sim.experiments.native_imu_rw_observability_r84r2 import run_native_case
from sat_sim.experiments.r84r3b_wheel_speed_measurement import WheelSpeedMeasurementAssumptions


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'configs/research/r84r4_independent_cohort_prereg_v1.json'
SOURCE = ROOT / 'src/sat_sim/experiments/native_imu_rw_observability_r84r2.py'
MEASUREMENT = ROOT / 'src/sat_sim/experiments/r84r3b_wheel_speed_measurement.py'
ANALYSIS = ROOT / 'scripts/analyze_r84r4_independent_cohort.py'


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def matches_frozen_text(path: Path, expected: str) -> bool:
    """Accept identical source content with either Git LF or Windows CRLF endings."""
    raw = path.read_bytes()
    lf = raw.replace(b'\r\n', b'\n')
    variants = (raw, lf, lf.replace(b'\n', b'\r\n'))
    return any(hashlib.sha256(value).hexdigest() == expected for value in variants)


def prior_seeds(path: Path) -> set[int]:
    with path.open(encoding='utf-8') as stream:
        return {json.loads(line)['rng_seed'] for line in stream if line.strip()}


def planned_cases(config: dict):
    d = config['design']
    for wheel in d['wheels']:
        for initial in d['initial_states']:
            for ref_index, reference in enumerate(d['reference_modes']):
                context = (wheel * 3 + initial) * 2 + ref_index
                for cohort_index, cohort in enumerate(d['cohorts']):
                    capacity = 'healthy' if cohort.startswith('healthy') else cohort.removesuffix('_test')
                    for repetition in range(d['repetitions_per_context_cohort']):
                        seed = 1800000000 + context * 100000 + cohort_index * 10000 + repetition
                        yield dict(wheel=wheel, initial=initial, reference=reference,
                                   cohort=cohort, capacity=capacity, repetition=repetition,
                                   context=context, seed=seed)


def run(output_dir: Path, old_r2: Path, pilot: Path) -> dict:
    config_bytes = CONFIG.read_bytes()
    config = json.loads(config_bytes)
    if config['status'] != 'FROZEN_BEFORE_NATIVE_EXECUTION':
        raise RuntimeError('preregistration status changed')
    if (not matches_frozen_text(SOURCE, config['source_module_sha256'])
            or not matches_frozen_text(MEASUREMENT, config['measurement_module_sha256'])
            or not matches_frozen_text(ANALYSIS, config['analysis_script_sha256'])):
        raise RuntimeError('native, measurement, or analysis source changed after preregistration')
    cases = list(planned_cases(config))
    seeds = [case['seed'] for case in cases]
    if len(cases) != config['design']['unique_native_cases'] or len(seeds) != len(set(seeds)):
        raise RuntimeError('cohort size or seed uniqueness violated')
    if set(seeds) & (prior_seeds(old_r2) | prior_seeds(pilot)):
        raise RuntimeError('new seed overlaps prior R2 or R4 pilot')
    params = WheelSpeedMeasurementAssumptions(**{key: value for key, value in config['wheel_measurement'].items() if key != 'basis'})
    output_dir.mkdir(parents=True, exist_ok=False)
    paths = {key: output_dir / f'r84r4_independent_{key}.jsonl.gz' for key in ('observations', 'imu', 'qa', 'cases')}
    handles = {key: gzip.open(path, 'wt', encoding='utf-8', newline='\n', compresslevel=6)
               for key, path in paths.items()}
    start = time.monotonic()
    complete = 0
    try:
        for case in cases:
            native = run_native_case(wheel=case['wheel'], initial=case['initial'],
                                     reference_mode=case['reference'], capacity_mode=case['capacity'],
                                     gyro_noise_std_rad_s=config['design']['gyro_noise_sigma_rad_s'], seed=case['seed'])
            observations = output_rows(native, params)
            rid = f'r84r4_cohort_{complete + 1:04d}'
            payloads = {
                'observations': {'record_id': rid, 'rows': observations},
                'imu': {'record_id': rid, 'rows': native['sensor_rows']},
                'qa': {'record_id': rid, 'rows': native['qa_rows']},
                'cases': {'record_id': rid, 'context_index': case['context'],
                          'wheel_index_QA_ONLY': case['wheel'], 'initial_state_index': case['initial'],
                          'reference_mode': case['reference'], 'cohort_QA_ONLY': case['cohort'],
                          'capacity_mode_QA_ONLY': case['capacity'], 'repetition': case['repetition'],
                          'rng_seed': case['seed'], 'native_receipts': native['native_event_receipts']},
            }
            for key, payload in payloads.items():
                handles[key].write(json.dumps(payload, sort_keys=True, allow_nan=False) + '\n')
            complete += 1
            if complete % 100 == 0:
                print(f'R4 native {complete}/{len(cases)} elapsed_s={time.monotonic() - start:.1f}', flush=True)
    finally:
        for handle in handles.values():
            handle.close()
    manifest = {'status': 'PASS', 'qualification': config['qualification'],
                'preregistration_sha256': hashlib.sha256(config_bytes).hexdigest(),
                'native_distribution': config['native_distribution'],
                'unique_native_cases': complete, 'expected_cases': len(cases),
                'seed_disjoint_from_r2_and_pilot': True,
                'artifact_sha256': {path.name: sha256(path) for path in paths.values()},
                'elapsed_seconds': round(time.monotonic() - start, 2)}
    (output_dir / 'r84r4_independent_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--prior-r2-cases', type=Path, required=True)
    parser.add_argument('--pilot-cases', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output_dir, args.prior_r2_cases, args.pilot_cases), indent=2))


if __name__ == '__main__':
    main()
