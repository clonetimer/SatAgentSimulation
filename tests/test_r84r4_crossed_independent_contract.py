"""Frozen R84-R4 crossed-axis plan and target-blind analysis contract."""
import json
from collections import Counter

from scripts.run_r84r4_crossed_independent import CONFIG, SOURCES, matches_frozen_text, planned_cases


def test_crossed_plan_is_frozen_and_seed_disjoint_by_construction():
    config = json.loads(CONFIG.read_text(encoding='utf-8'))
    assert config['status'] == 'FROZEN_BEFORE_EXECUTION'
    assert all(matches_frozen_text(path, config[key]) for key, path in SOURCES.items())
    plan = list(planned_cases(config))
    assert len(plan) == len({case['seed'] for case in plan}) == 7560
    assert min(case['seed'] for case in plan) >= 2000000000
    assert max(case['seed'] for case in plan) < 2**31
    cells = Counter((case['noise'], case['axis'], case['wheel'], case['initial'], case['cohort'])
                    for case in plan)
    assert len(cells) == 324
    assert all(count == (30 if noise == config['design']['primary_imu_sigma_rad_s'] else 20)
               for (noise, *_), count in cells.items())
    assert config['analysis']['hidden_at_inference'] == ['fault_target_wheel', 'capacity_mode', 'physics_QA']
