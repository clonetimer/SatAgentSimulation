from __future__ import annotations

import unittest

from validation.legacy.whole_spacecraft.propulsion_unified_feasibility import (
    PropulsionUnifiedFeasibilityConfig,
    run_propulsion_unified_feasibility,
)


class WholeSubsystemOwnedFeasibilityTests(unittest.TestCase):
    def test_outer_timing_contract_keeps_terminal_states_valid(self) -> None:
        cfg = PropulsionUnifiedFeasibilityConfig(
            duration_s=20.0,
            sample_s=10.0,
            propulsion_enabled=False,
            max_final_attitude_ratio=99.0,
        )
        summary, rows = run_propulsion_unified_feasibility(cfg)
        self.assertEqual([row.time_s for row in rows], [0.0, 10.0, 20.0])
        self.assertEqual(summary.status, "PASS")
        self.assertGreater(rows[-1].battery_soc, 0.0)
        self.assertGreater(rows[-1].data_storage_bits, 0.0)
        self.assertGreater(abs(rows[-1].position_x_m), 0.0)
        self.assertTrue(summary.message_ownership_status.startswith("PASS_"))


if __name__ == "__main__":
    unittest.main()
