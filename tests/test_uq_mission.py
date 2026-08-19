"""Unit tests for the V17 UQ harness that avoid running Basilisk."""
from __future__ import annotations

import unittest

from validation.uq_mission import (
    _unit_lhs,
    _sample_value,
    _quantile,
    apply_sample_to_structure,
    default_design,
)
from whole_spacecraft.schemas import WholeSpacecraftConfig


class TestUQMissionHarness(unittest.TestCase):
    def test_lhs_is_deterministic_and_stratified(self) -> None:
        a = _unit_lhs(4, 3, 123)
        b = _unit_lhs(4, 3, 123)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 4)
        self.assertEqual(len(a[0]), 3)
        for column in zip(*a):
            bins = sorted(int(v * 4) for v in column)
            self.assertEqual(bins, [0, 1, 2, 3])

    def test_sampling_and_config_application(self) -> None:
        design = default_design()
        factors = {spec.parameter_id: 1.0 for spec in design.parameters}
        # absolute distributions expect actual value, not a factor.
        factors["whole.initial_soc"] = 0.61
        structure, sampled = apply_sample_to_structure(WholeSpacecraftConfig(), design.parameters, factors)
        self.assertAlmostEqual(structure.battery_capacity_wh, 160.0)
        self.assertAlmostEqual(structure.initial_soc, 0.61)
        self.assertIn("whole.native_downlink_bit_rate_request_bps", sampled)
        self.assertAlmostEqual(structure.transmitter_baud_bps, structure.native_downlink_bit_rate_request_bps)

    def test_quantile_interpolation(self) -> None:
        self.assertEqual(_quantile([1.0, 2.0, 3.0], 0.5), 2.0)
        self.assertAlmostEqual(_quantile([10.0, 20.0], 0.25), 12.5)

    def test_log_uniform_sample_positive(self) -> None:
        spec = next(s for s in default_design().parameters if s.parameter_id == "whole.native_downlink_cnr_linear")
        self.assertGreater(_sample_value(spec, 0.0), 0.0)
        self.assertGreater(_sample_value(spec, 1.0), _sample_value(spec, 0.0))


if __name__ == "__main__":
    unittest.main()
