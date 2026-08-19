from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest

from parameters.evidence import evidence_compatibility, uncertainty_is_engineering_ready
from parameters.provenance import load_parameter_records, validate_parameter_registry

ROOT = Path(__file__).resolve().parents[1]


class ParameterEvidenceTests(unittest.TestCase):
    def test_source_confidence_ceiling(self) -> None:
        self.assertFalse(evidence_compatibility("synthetic_only", "engineering_estimate").source_allows_confidence)
        self.assertTrue(evidence_compatibility("bounded_assumption", "engineering_estimate").source_allows_confidence)
        self.assertFalse(evidence_compatibility("bounded_assumption", "ground_calibrated").source_allows_confidence)
        self.assertTrue(evidence_compatibility("formal_ground_test", "ground_calibrated").source_allows_confidence)

    def test_uncertainty_gate(self) -> None:
        self.assertTrue(uncertainty_is_engineering_ready(5.0, {"distribution": "bounded", "lower": 4.0, "upper": 6.0}))
        self.assertFalse(uncertainty_is_engineering_ready(5.0, {"distribution": "not_established"}))
        self.assertTrue(uncertainty_is_engineering_ready("fixed", {"distribution": "categorical_fixed", "options": ["fixed"]}))

    def test_engineering_registry_qualifies_engineering_only(self) -> None:
        payload, records = load_parameter_records(ROOT / "configs" / "parameters" / "engineering_estimate_registry_v1.json")
        engineering = validate_parameter_registry(payload, records, requested_profile="engineering_estimate")
        ground = validate_parameter_registry(payload, records, requested_profile="ground_calibrated")
        self.assertEqual(engineering["status"], "PASS")
        self.assertEqual(engineering["confidence_counts"]["engineering_estimate"], len(records))
        self.assertEqual(ground["status"], "FAIL")

    def test_synthetic_forgery_is_rejected(self) -> None:
        payload, records = load_parameter_records(ROOT / "configs" / "parameters" / "engineering_estimate_registry_v1.json")
        forged = [replace(records[0], source_type="synthetic_only", confidence="engineering_estimate", source_ref="synthetic://unit-test")]
        gate = validate_parameter_registry(payload, forged, requested_profile="engineering_estimate", required_ids=(forged[0].parameter_id,))
        self.assertEqual(gate["status"], "FAIL")
        self.assertTrue(any(issue["code"] == "source_confidence_ceiling_exceeded" for issue in gate["issues"]))


if __name__ == "__main__":
    unittest.main()
