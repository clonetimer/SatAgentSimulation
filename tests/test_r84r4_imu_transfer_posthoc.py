"""Guard target-blind versus target-conditioned IMU transfer comparison."""
from __future__ import annotations

from scripts import analyze_r84r4_imu_transfer_posthoc as transfer
from scripts import run_r84r2_multipulse_multifeature_observability as r2


def test_target_blind_calibrates_without_target_wheel_identity():
    records = []
    for wheel in range(3):
        for cohort in ('healthy_calibration', 'healthy_control', 'loss50_test', 'loss75_test'):
            for repetition in range(30):
                value = wheel * 0.01 + repetition * 0.00001
                records.append({'wheel': wheel, 'initial': 0, 'reference': 'pulse09x4',
                                'cohort': cohort,
                                'feature': {field: value for field in r2.FEATURE_FIELDS}})
    blind = transfer.evaluate(records, target_blind=True)
    conditional = transfer.evaluate(records, target_blind=False)
    assert blind['target_wheel_used_to_choose_calibration'] is False
    assert len(blind['per_group']) == 1 and blind['per_group'][0]['calibration_size'] == 90
    assert conditional['target_wheel_used_to_choose_calibration'] is True
    assert len(conditional['per_group']) == 3
    assert {group['calibration_size'] for group in conditional['per_group']} == {30}
    assert all(counts['total'] == 90 for counts in blind['overall'].values())
