"""Project pinned R84-R2 native records into R84-R4 observation candidates.

用途：从已封存的 R2 IMU 与原生 QA 记录只提取 IMU、三轮速度和 FSW 命令，施加显式 R3B 测量敏感性假设。
参数：--source-dir、--profile、--output-dir。
输出：只含观测字段的 JSONL 与来源/行数/哈希收据；不运行新仿真，不训练模型，不授予独立确认或数据集资格。
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from sat_sim.experiments.r84r3b_wheel_speed_measurement import (
    WheelSpeedMeasurementAssumptions, observe_wheel_speeds,
)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def jsonl(path: Path):
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def project(source_dir: Path, profile_path: Path, output_dir: Path) -> dict:
    raw = (source_dir / 'r84r2_execution_manifest.json').read_bytes()
    profile_raw = profile_path.read_bytes()
    profile = json.loads(profile_raw)
    if profile.get('qualification') != 'exploratory_reuse_not_independent_confirmation':
        raise ValueError('R4 exploratory qualification must be explicit')
    if profile.get('source_r84r2_manifest_sha256') != hashlib.sha256(raw).hexdigest():
        raise ValueError('R2 source manifest differs from frozen profile')
    manifest = json.loads(raw)
    for name, expected in manifest['artifacts'].items():
        path = (source_dir / name).resolve()
        if not path.is_relative_to(source_dir.resolve()) or sha(path) != expected:
            raise ValueError(f'R2 source hash mismatch: {name}')
    params = WheelSpeedMeasurementAssumptions(**profile['parameters'])
    output_dir.mkdir(parents=True, exist_ok=False)
    target = output_dir / 'r84r4_observation_candidates.jsonl'
    rows_written = missing = 0
    counts = Counter()
    ids = set()
    replay_rows = None
    original_replay_id = None
    with target.open('w', encoding='utf-8', newline='\n') as sink:
        sources = zip(
            jsonl(source_dir / 'r84r2_case_manifest.jsonl'),
            jsonl(source_dir / 'r84r2_sensor_observations.jsonl'),
            jsonl(source_dir / 'r84r2_physics_qa.jsonl'), strict=True,
        )
        for case, sensor, qa in sources:
            rid = case['record_id']
            if rid in ids or rid != sensor['record_id'] or rid != qa['record_id']:
                raise ValueError('R2 record alignment or uniqueness failed')
            ids.add(rid)
            if len(sensor['rows']) != len(qa['qa_rows']) or len(sensor['rows']) != 91:
                raise ValueError('R2 sample count mismatch')
            source_rows = []
            for raw_sensor, truth in zip(sensor['rows'], qa['qa_rows'], strict=True):
                if abs(raw_sensor['time_s']-truth['time_s']) > 1e-12:
                    raise ValueError('R2 sensor/QA clocks differ')
                observation = dict(raw_sensor)
                for wheel in range(3):
                    observation[f'sensor.adcs.rw.speed_rad_s_{wheel}'] = truth[f'rw.native_speed_rad_s_{wheel}']
                    observation[f'command.adcs.rw.motor_torque_nm_{wheel}'] = truth[f'rw.native_allocator_request_nm_{wheel}']
                source_rows.append(observation)
            canonical = case.get('replay_of_record_id', rid)
            seed = int.from_bytes(hashlib.sha256((profile['seed_namespace']+':'+canonical).encode()).digest()[:7], 'big')
            wheel_rows = observe_wheel_speeds(source_rows, params, seed=seed)
            output_rows = []
            for index, measured in enumerate(wheel_rows):
                native = source_rows[index * params.sample_every_n_native_points]
                result = {key:value for key,value in native.items() if not key.startswith('sensor.adcs.rw.speed_rad_s_')}
                result.update(measured)
                if any('QA_ONLY' in key or 'fault' in key or 'label' in key or key.startswith('hub.') for key in result):
                    raise ValueError('R4 model observation contains physical truth')
                output_rows.append(result)
                missing += sum(not result[f'wheel_{wheel}_valid'] for wheel in range(3))
            if case.get('repeatability_control'):
                original_replay_id = canonical
                replay_rows = output_rows
            sink.write(json.dumps({'record_id':rid,'rows':output_rows},sort_keys=True,allow_nan=False)+'\n')
            counts[case['cohort']] += 1
            rows_written += len(output_rows)
    if len(ids) != 2863 or sum(counts.values()) != 2863 or original_replay_id is None:
        raise ValueError('R2 case/replay count mismatch')
    # Replay source is checked from the finished file, independent of processing order.
    by_id = {item['record_id']:item['rows'] for item in jsonl(target) if item['record_id'] in {original_replay_id, rid}}
    if by_id.get(original_replay_id) != replay_rows:
        raise ValueError('R4 measurement replay differs from original seed')
    report = {'status':'PASS','qualification':profile['qualification'],'formal_dataset_eligible':False,
              'source_manifest_sha256':hashlib.sha256(raw).hexdigest(),
              'profile_sha256':hashlib.sha256(profile_raw).hexdigest(),
              'output_sha256':sha(target),'native_source_cases':len(ids),'observation_rows':rows_written,
              'explicit_missing_wheel_values':missing,'cohort_counts_qa_only':dict(counts),
              'seeded_replay_exact':True,'labels_and_physics_truth_excluded':True,
              'output_channels':['imu.raw_gyro_rad_s_x/y/z','sensor.adcs.rw.speed_rad_s_0/1/2',
                                 'command.adcs.rw.motor_torque_nm_0/1/2','time_s','source_time_s',
                                 'wheel_0/1/2_valid','wheel_0/1/2_receipt_time_s']}
    (output_dir/'r84r4_projection_manifest.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir',type=Path,required=True)
    parser.add_argument('--profile',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args = parser.parse_args()
    print(json.dumps(project(args.source_dir,args.profile,args.output_dir),indent=2))


if __name__ == '__main__':
    main()
