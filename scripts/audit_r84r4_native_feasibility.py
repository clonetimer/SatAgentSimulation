"""Independently audit the bounded R84-R4 genuine-native feasibility output.

用途：逐行核验新 Basilisk 工况的 IMU、轮速、FSW 命令来源、种子、QA 隔离及同 seed 回放。
参数：--evidence-dir、--prior-r2-cases、--report。
输出：机器可读审计收据；只验证可行性，不计算诊断指标或授予数据资格。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ALLOWED = {'time_s','source_time_s',*(f'imu.raw_gyro_rad_s_{a}' for a in 'xyz'),
           *(f'sensor.adcs.rw.speed_rad_s_{i}' for i in range(3)),
           *(f'command.adcs.rw.motor_torque_nm_{i}' for i in range(3)),
           *(f'wheel_{i}_valid' for i in range(3)),
           *(f'wheel_{i}_receipt_time_s' for i in range(3))}


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(4*1024*1024),b''):
            h.update(data)
    return h.hexdigest()


def jsonl(path):
    with path.open(encoding='utf-8') as stream:
        return [json.loads(line) for line in stream if line.strip()]


def audit(directory, prior):
    manifest = json.loads((directory/'r84r4_feasibility_manifest.json').read_text(encoding='utf-8'))
    assert manifest['status'] == 'PASS' and manifest['unique_native_cases'] == 54
    for name,digest in manifest['artifact_sha256'].items():
        assert sha(directory/name) == digest, name
    records = {key:jsonl(directory/f'r84r4_{key}.jsonl') for key in ('observation','imu','qa','cases')}
    assert all(len(items)==55 for items in records.values())
    previous = {case['rng_seed'] for case in jsonl(prior)}
    novel = set()
    context = set()
    missing = 0
    replay = None
    by_id = {}
    for observed,imu,qa,case in zip(*(records[k] for k in ('observation','imu','qa','cases')),strict=True):
        rid = case['record_id']
        assert rid == observed['record_id'] == imu['record_id'] == qa['record_id']
        assert len(observed['rows']) == 46 and len(imu['rows']) == len(qa['rows']) == 91
        if case.get('repeatability_control'):
            replay = (case,observed,imu,qa)
        else:
            assert case['rng_seed'] not in previous and case['rng_seed'] not in novel
            novel.add(case['rng_seed'])
            context.add((case['wheel_index_QA_ONLY'],case['initial_state_index'],
                         case['reference_mode'],case['capacity_mode_QA_ONLY']))
            by_id[rid] = (observed,imu,qa,case)
        for index,row in enumerate(observed['rows']):
            raw = imu['rows'][index*2]
            truth = qa['rows'][index*2]
            assert set(row) == ALLOWED and row['time_s'] == row['source_time_s'] == raw['time_s'] == truth['time_s']
            for axis in 'xyz':
                assert row[f'imu.raw_gyro_rad_s_{axis}'] == raw[f'imu.raw_gyro_rad_s_{axis}']
            for wheel in range(3):
                assert row[f'command.adcs.rw.motor_torque_nm_{wheel}'] == truth[f'rw.native_allocator_request_nm_{wheel}']
                valid = row[f'wheel_{wheel}_valid']
                assert type(valid) is bool and valid == (row[f'sensor.adcs.rw.speed_rad_s_{wheel}'] is not None)
                assert row[f'wheel_{wheel}_receipt_time_s'] >= row['time_s']
                missing += not valid
    assert len(novel) == len(context) == 54 and replay is not None
    case,observed,imu,qa = replay
    source = by_id[case['replay_of_record_id']]
    assert case['rng_seed'] == source[3]['rng_seed']
    assert observed['rows'] == source[0]['rows'] and imu['rows'] == source[1]['rows'] and qa['rows'] == source[2]['rows']
    result = {'status':'PASS','qualification':'new_native_feasibility_only_not_diagnostic_effect',
              'unique_new_seeds':len(novel),'seed_disjoint_from_r2':True,'physical_contexts':len(context),
              'native_executions_including_replay':55,'native_rows':55*91,'observation_rows':55*46,
              'missing_wheel_values_including_replay':missing,'exact_replay':True,
              'imu_and_command_native_source_match':True,'qa_and_labels_excluded':True,
              'artifact_sha256':manifest['artifact_sha256']}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir',type=Path,required=True)
    parser.add_argument('--prior-r2-cases',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args = parser.parse_args()
    receipt = audit(args.evidence_dir,args.prior_r2_cases)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(receipt,indent=2))


if __name__ == '__main__':
    main()
