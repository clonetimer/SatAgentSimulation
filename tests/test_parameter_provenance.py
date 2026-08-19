from __future__ import annotations

from pathlib import Path
import unittest

from parameters.provenance import build_parameter_provenance_payload, required_parameter_ids

ROOT = Path(__file__).resolve().parents[1]


class ParameterProvenanceTests(unittest.TestCase):
    def test_demo_registry_covers_required_parameters(self) -> None:
        payload = build_parameter_provenance_payload(ROOT, requested_profile="demo")
        summary = payload["summary"]
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(summary["missing_parameter_count"], 0)
        self.assertGreaterEqual(summary["record_count"], len(required_parameter_ids()))
        self.assertEqual(summary["minimum_observed_confidence"], "demo")

    def test_demo_registry_does_not_qualify_engineering_claim(self) -> None:
        payload = build_parameter_provenance_payload(ROOT, requested_profile="engineering_estimate")
        summary = payload["summary"]
        self.assertEqual(payload["status"], "FAIL")
        self.assertGreater(summary["invalid_parameter_count"], 0)
        self.assertTrue(any(issue["code"] == "insufficient_confidence" for issue in summary["issues"]))

    def test_parameter_records_have_units_and_targets(self) -> None:
        payload = build_parameter_provenance_payload(ROOT, requested_profile="demo")
        for row in payload["parameters"]:
            self.assertTrue(row["unit"])
            self.assertTrue(row["config_path"])
            self.assertTrue(row["native_target"])


if __name__ == "__main__":
    unittest.main()
