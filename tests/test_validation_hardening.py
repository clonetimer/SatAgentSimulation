from __future__ import annotations

import math
import unittest
from types import SimpleNamespace

from scripts.run_basilisk_validation_suite import validation_result_from_summary
from whole_spacecraft.runner import _finite_rows_ok


class ValidationHardeningTests(unittest.TestCase):
    def test_partial_status_is_not_formal_pass(self) -> None:
        self.assertEqual(
            validation_result_from_summary({"status": "PASS_WITH_PARTIAL_QOI", "partial_qoi_count": 2}, returncode=0),
            "PARTIAL",
        )

    def test_runtime_fault_fallback_is_failure(self) -> None:
        self.assertEqual(
            validation_result_from_summary({"status": "PASS", "runtime_fault_fallback_count": 1}, returncode=0),
            "FAIL",
        )

    def test_runtime_fault_without_trigger_is_failure(self) -> None:
        self.assertEqual(
            validation_result_from_summary(
                {
                    "status": "PASS",
                    "runtime_fault_event_count": 2,
                    "runtime_fault_triggered_count": 1,
                    "runtime_fault_mutation_target_count": 3,
                },
                returncode=0,
            ),
            "FAIL",
        )

    def test_transient_fault_without_successful_recovery_is_failure(self) -> None:
        base = {
            "status": "PASS",
            "runtime_fault_event_count": 1,
            "runtime_fault_triggered_count": 1,
            "runtime_fault_mutation_target_count": 1,
            "runtime_fault_recovery_expected_count": 1,
            "runtime_fault_recovery_registered_count": 1,
            "runtime_fault_recovery_triggered_count": 1,
            "runtime_fault_recovery_failure_count": 0,
        }
        self.assertEqual(validation_result_from_summary(base, returncode=0), "PASS")
        for field, value in (
            ("runtime_fault_recovery_registered_count", 0),
            ("runtime_fault_recovery_triggered_count", 0),
            ("runtime_fault_recovery_failure_count", 1),
        ):
            failed = dict(base)
            failed[field] = value
            self.assertEqual(validation_result_from_summary(failed, returncode=0), "FAIL")

    def test_whole_spacecraft_finite_gate_rejects_nan_rows(self) -> None:
        good = SimpleNamespace(
            battery_storage_j=1.0,
            battery_soc=0.5,
            net_power_w=10.0,
            data_storage_bits=1.0,
            thermal_temp_c=20.0,
            thermal_margin_c=5.0,
            attitude_error_norm=0.1,
            rate_error_norm_rad_s=0.01,
            propellant_remaining_kg=1.0,
        )
        bad = SimpleNamespace(**{**good.__dict__, "attitude_error_norm": math.nan})
        self.assertTrue(_finite_rows_ok([good]))
        self.assertFalse(_finite_rows_ok([bad]))


if __name__ == "__main__":
    unittest.main()
