from __future__ import annotations

from pathlib import Path
import unittest

from sat_sim.validation.layer_schema_contract import build_layer_schema_contract


ROOT = Path(__file__).resolve().parents[1]


class LayerSchemaContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = build_layer_schema_contract(ROOT)

    def test_contract_passes(self) -> None:
        self.assertEqual(self.payload["status"], "PASS")
        self.assertEqual(self.payload["summary"]["error_count"], 0)

    def test_whole_spacecraft_has_no_component_bypass(self) -> None:
        summary = self.payload["summary"]
        self.assertEqual(summary["whole_component_import_count"], 0)
        self.assertEqual(summary["whole_unauthorized_native_import_count"], 0)
        self.assertEqual(summary["whole_native_constructor_count"], 0)
        self.assertEqual(summary["runtime_fault_router_status"], "PASS")

    def test_component_contracts_live_in_schemas(self) -> None:
        summary = self.payload["summary"]
        self.assertGreaterEqual(summary["component_schema_surface_count"], 20)
        self.assertEqual(summary["builder_contract_definition_count"], 0)
        self.assertEqual(summary["schema_import_cycle_count"], 0)
        self.assertEqual(summary["builder_missing_schema_import_count"], 0)
        self.assertEqual(summary["missing_component_module_docstring_count"], 0)

    def test_no_unregistered_ghost_parameter(self) -> None:
        summary = self.payload["summary"]
        self.assertEqual(summary["unregistered_ghost_parameter_count"], 0)
        self.assertEqual(summary["registered_known_ghost_parameter_count"], 0)

    def test_required_builders_do_not_suppress_wiring_errors(self) -> None:
        self.assertEqual(self.payload["summary"]["silent_builder_or_whole_exception_count"], 0)


if __name__ == "__main__":
    unittest.main()
