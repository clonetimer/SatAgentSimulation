"""Run the frozen R84-R4 native multichannel feasibility matrix.

用途：以新独立 seed 执行 54 个真实 Basilisk 工况及一次同 seed 回放，并将 IMU、测量轮速、FSW 命令与物理 QA 分开封存。
参数：--output-dir 指定全新证据目录。
输出：观测 JSONL、原始 IMU JSONL、物理 QA JSONL、工况清单和 SHA-256 收据；不计算诊断准确率或数据集资格。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sat_sim.experiments.native_imu_rw_observability_r84r2 import run_native_case
from sat_sim.experiments.r84r3b_wheel_speed_measurement import (
    WheelSpeedMeasurementAssumptions, observe_wheel_speeds,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT/'configs/research/r84r4_native_multichannel_feasibility_prereg_v1.json'
OUTPUT_KEYS = {'time_s','source_time_s',*(f'imu.raw_gyro_rad_s_{a}' for a in 'xyz'),
               *(f'sensor.adcs.rw.speed_rad_s_{i}' for i in range(3)),
               *(f'command.adcs.rw.motor_torque_nm_{i}' for i in range(3)),
               *(f'wheel_{i}_valid' for i in range(3)),
               *(f'wheel_{i}_receipt_time_s' for i in range(3))}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def seed_of(wheel, initial, reference_index, capacity_index):
    return 1700000000 + wheel*1000000 + initial*100000 + reference_index*10000 + capacity_index*1000 + 17


def output_rows(native, params):
    source = []
    for sensor, truth in zip(native['sensor_rows'], native['qa_rows'], strict=True):
        assert sensor['time_s'] == truth['time_s']
        row = dict(sensor)
        for wheel in range(3):
            row[f'sensor.adcs.rw.speed_rad_s_{wheel}'] = truth[f'rw.native_speed_rad_s_{wheel}']
            row[f'command.adcs.rw.motor_torque_nm_{wheel}'] = truth[f'rw.native_allocator_request_nm_{wheel}']
        source.append(row)
    measured = observe_wheel_speeds(source, params, seed=native['rng_seed'])
    rows = []
    for index, values in enumerate(measured):
        raw = source[index*params.sample_every_n_native_points]
        result = {key:value for key,value in raw.items() if not key.startswith('sensor.adcs.rw.speed_rad_s_')}
        result.update(values)
        if set(result) != OUTPUT_KEYS:
            raise RuntimeError('R4 observation allowlist mismatch')
        rows.append(result)
    return rows


def run(output_dir):
    config_bytes = CONFIG.read_bytes()
    config = json.loads(config_bytes)
    if config['status'] != 'PREREGISTERED_NOT_EXECUTED' or config['design']['unique_native_cases'] != 54:
        raise RuntimeError('R4 frozen design invalid')
    params = WheelSpeedMeasurementAssumptions(**{k:v for k,v in config['wheel_measurement'].items() if k != 'basis'})
    output_dir.mkdir(parents=True, exist_ok=False)
    paths = {key:output_dir/f'r84r4_{key}.jsonl' for key in ('observation','imu','qa','cases')}
    handles = {key:path.open('w',encoding='utf-8',newline='\n') for key,path in paths.items()}
    counts = {'unique':0,'replay':0,'missing':0}
    first_replay = None
    try:
        for wheel in config['design']['wheels']:
            for initial in config['design']['initial_states']:
                for ref_index, reference in enumerate(config['design']['reference_modes']):
                    for cap_index, capacity in enumerate(config['design']['capacity_modes']):
                        seed = seed_of(wheel,initial,ref_index,cap_index)
                        native = run_native_case(wheel=wheel,initial=initial,reference_mode=reference,
                                                 capacity_mode=capacity,gyro_noise_std_rad_s=config['design']['gyro_noise_sigma_rad_s'],seed=seed)
                        rid = f'r84r4_{counts["unique"]+1:03d}'
                        observed = output_rows(native,params)
                        for key,payload in (
                            ('observation',{'record_id':rid,'rows':observed}),
                            ('imu',{'record_id':rid,'rows':native['sensor_rows']}),
                            ('qa',{'record_id':rid,'rows':native['qa_rows']}),
                            ('cases',{'record_id':rid,'wheel_index_QA_ONLY':wheel,'initial_state_index':initial,
                                      'reference_mode':reference,'capacity_mode_QA_ONLY':capacity,'rng_seed':seed,
                                      'native_receipts':native['native_event_receipts']}),
                        ):
                            handles[key].write(json.dumps(payload,sort_keys=True,allow_nan=False)+'\n')
                        counts['missing'] += sum(not row[f'wheel_{i}_valid'] for row in observed for i in range(3))
                        counts['unique'] += 1
                        if (wheel,initial,reference,capacity) == (2,2,'pulse09x4','loss75'):
                            first_replay = (native,observed,rid)
        assert first_replay is not None
        native,observed,source_id = first_replay
        replay = run_native_case(wheel=2,initial=2,reference_mode='pulse09x4',capacity_mode='loss75',
                                 gyro_noise_std_rad_s=config['design']['gyro_noise_sigma_rad_s'],seed=native['rng_seed'])
        reread = output_rows(replay,params)
        if reread != observed or replay['sensor_rows'] != native['sensor_rows'] or replay['qa_rows'] != native['qa_rows']:
            raise RuntimeError('R4 exact seeded replay mismatch')
        rid = 'r84r4_seeded_replay'
        for key,payload in (
            ('observation',{'record_id':rid,'rows':reread}),
            ('imu',{'record_id':rid,'rows':replay['sensor_rows']}),
            ('qa',{'record_id':rid,'rows':replay['qa_rows']}),
            ('cases',{'record_id':rid,'replay_of_record_id':source_id,'rng_seed':native['rng_seed'],
                      'repeatability_control':True}),
        ):
            handles[key].write(json.dumps(payload,sort_keys=True,allow_nan=False)+'\n')
        counts['replay'] = 1
    finally:
        for stream in handles.values():
            stream.close()
    report = {'status':'PASS','qualification':config['qualification'],'formal_dataset_eligible':False,
              'preregistration_sha256':hashlib.sha256(config_bytes).hexdigest(),
              'native_distribution':config['native_distribution'],
              'unique_native_cases':counts['unique'],'exact_replays':counts['replay'],
              'samples_per_native_case':91,'samples_per_observation_case':46,
              'missing_wheel_values_unique_cases':counts['missing'],
              'artifact_sha256':{path.name:digest(path) for path in paths.values()}}
    (output_dir/'r84r4_feasibility_manifest.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output_dir),indent=2))


if __name__ == '__main__':
    main()
