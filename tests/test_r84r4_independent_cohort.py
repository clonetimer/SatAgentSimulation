"""Focused safeguards for the frozen R84-R4 independent cohort analysis."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


def _module(name: str):
    path = ROOT / 'scripts' / f'{name}.py'
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_predeclared_seeds_have_four_disjoint_cohorts(tmp_path):
    import json
    runner = _module('run_r84r4_independent_cohort')
    config = json.loads(runner.CONFIG.read_text(encoding='utf-8'))
    cases = list(runner.planned_cases(config))
    assert len(cases) == len({case['seed'] for case in cases}) == 2160
    assert len({(case['context'], case['cohort']) for case in cases}) == 72
    assert min(case['seed'] for case in cases) >= 1800000000
    assert max(case['seed'] for case in cases) < 2**31
    assert runner.matches_frozen_text(runner.SOURCE, config['source_module_sha256'])
    assert runner.matches_frozen_text(runner.MEASUREMENT, config['measurement_module_sha256'])
    assert runner.matches_frozen_text(runner.ANALYSIS, config['analysis_script_sha256'])
    normalized = tmp_path / 'source.py'
    normalized.write_bytes(runner.SOURCE.read_bytes().replace(b'\r\n', b'\n'))
    assert runner.matches_frozen_text(normalized, config['source_module_sha256'])


def test_response_gain_abstains_without_excitation_and_ignores_qa_fields():
    analysis = _module('analyze_r84r4_independent_cohort')
    rows = []
    for index in range(46):
        row = {'source_time_s': index * 0.4, 'capacity_mode_QA_ONLY': 'loss75'}
        for wheel in range(3):
            row[f'wheel_{wheel}_valid'] = True
            row[f'sensor.adcs.rw.speed_rad_s_{wheel}'] = index * (wheel + 1) * 0.1
            row[f'command.adcs.rw.motor_torque_nm_{wheel}'] = 0.0
        rows.append(row)
    assert analysis.gains(rows) is None
    for row in rows:
        for wheel in range(3):
            row[f'command.adcs.rw.motor_torque_nm_{wheel}'] = 0.01
    with_qa = analysis.gains(rows)
    for row in rows:
        row.pop('capacity_mode_QA_ONLY')
    assert with_qa == analysis.gains(rows)
    assert with_qa is not None


def test_nearest_rank_frozen_threshold():
    analysis = _module('analyze_r84r4_independent_cohort')
    assert analysis.nearest_rank(list(range(90)), 0.9) == 80
