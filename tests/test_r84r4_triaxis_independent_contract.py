"""Frozen R84-R4 three-axis independent design and source-only scoring input."""
import json
from collections import Counter
from scripts.analyze_r84r4_triaxis_independent import imu_blocks, wheel_blocks
from scripts.run_r84r4_triaxis_independent import CONFIG, SOURCES, matches_frozen_text, planned_cases


def test_triaxis_independent_plan_and_sources_are_frozen():
    config = json.loads(CONFIG.read_text(encoding='utf-8'))
    assert config['status'] == 'FROZEN_BEFORE_EXECUTION'
    assert all(matches_frozen_text(path, config[key]) for key, path in SOURCES.items())
    plan = list(planned_cases(config))
    assert len(plan) == len({case['seed'] for case in plan}) == 7560
    assert min(case['seed'] for case in plan) >= 2040000000
    assert max(case['seed'] for case in plan) < 2**31
    cells = Counter((case['noise'], case['order'], case['wheel'], case['initial'], case['cohort'])
                    for case in plan)
    assert len(cells) == 324
    assert all(count == (30 if noise == config['design']['primary_imu_sigma_rad_s'] else 20)
               for (noise, *_), count in cells.items())
    assert config['analysis']['hidden_at_inference'] == ['fault_target_wheel', 'capacity_mode', 'physics_QA']


def test_three_segments_use_only_recorded_observations():
    imu = [{'time_s': index * .2, **{f'imu.raw_gyro_rad_s_{axis}': .001 * (index + offset)
                                           for offset, axis in enumerate('xyz')}} for index in range(271)]
    observed = []
    for index in range(136):
        row = {'source_time_s': index * .4}
        for wheel in range(3):
            row[f'wheel_{wheel}_valid'] = True
            row[f'sensor.adcs.rw.speed_rad_s_{wheel}'] = index * (wheel + 1) * .1
            row[f'command.adcs.rw.motor_torque_nm_{wheel}'] = .01
        observed.append(row)
    assert len(imu_blocks(imu)) == 3
    assert all(len(block) == 16 for block in imu_blocks(imu))
    assert len(wheel_blocks(observed, 2)) == 9
