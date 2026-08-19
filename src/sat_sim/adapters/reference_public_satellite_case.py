"""Adapter for public-reference satellite case lookup.

SAT-REF-1 exposes curated public-reference-informed case metadata to Agent
workflows.  It does not run a simulation and does not claim that any packaged
case is flight validated.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from sat_sim.adapter_base import SimulationResult
from sat_sim.task_validator import ValidationIssue

ROOT = Path(__file__).resolve().parents[3]
REFERENCE_ROOT = ROOT / "reference_cases" / "public_satellite"


class PublicSatelliteReferenceCaseAdapter:
    """Return public-reference satellite case metadata."""

    capability_id = "reference.public_satellite_case.v1"

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if spec.get("capability_id") != self.capability_id:
            issues.append(ValidationIssue("error", "$.capability_id", f"must be {self.capability_id!r}", "capability"))
        if spec.get("task_type") not in {"reference", "dataset", "support"}:
            issues.append(ValidationIssue("error", "$.task_type", "public satellite reference capability requires task_type='reference'", "capability"))
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        case_id = params.get("case_id", "public_1u_cubesat_thermal_power_orbit")
        if not isinstance(case_id, str) or not case_id.strip():
            issues.append(ValidationIssue("error", "$.parameters.case_id", "must be a non-empty string", "type"))
        inventory_path = REFERENCE_ROOT / "case_inventory.json"
        if not REFERENCE_ROOT.exists():
            issues.append(ValidationIssue("error", "$.reference_cases", "reference_cases/public_satellite directory is missing", "artifact"))
        elif not inventory_path.exists():
            issues.append(ValidationIssue("error", "$.reference_cases.case_inventory", "case_inventory.json is missing", "artifact"))
        else:
            try:
                inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
                cases = inventory.get("cases", []) if isinstance(inventory, Mapping) else []
                known_case_ids = {
                    str(case.get("case_id"))
                    for case in cases
                    if isinstance(case, Mapping) and isinstance(case.get("case_id"), str) and str(case.get("case_id")).strip()
                }
                if case_id not in known_case_ids:
                    known = ", ".join(sorted(known_case_ids)) or "<none>"
                    issues.append(ValidationIssue("error", "$.parameters.case_id", f"unknown public satellite case_id {case_id!r}; known: {known}", "artifact"))
                else:
                    selected = next(case for case in cases if isinstance(case, Mapping) and case.get("case_id") == case_id)
                    asset_path = selected.get("asset_path") if isinstance(selected, Mapping) else None
                    if not isinstance(asset_path, str) or not asset_path.strip():
                        issues.append(ValidationIssue("error", "$.reference_cases.asset_path", f"case {case_id!r} has no asset_path", "artifact"))
                    elif not (REFERENCE_ROOT / asset_path).is_file():
                        issues.append(ValidationIssue("error", "$.reference_cases.asset_path", f"case asset is missing: {asset_path}", "artifact"))
            except Exception as exc:
                issues.append(ValidationIssue("error", "$.reference_cases.case_inventory", f"invalid case inventory: {exc}", "artifact"))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        case_id = str(params.get("case_id", "public_1u_cubesat_thermal_power_orbit"))
        inventory_path = REFERENCE_ROOT / "case_inventory.json"
        if not inventory_path.exists():
            raise FileNotFoundError(f"public satellite case inventory is missing: {inventory_path}")
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        if not isinstance(inventory, Mapping) or not isinstance(inventory.get("cases"), list):
            raise ValueError(f"public satellite case inventory is malformed: {inventory_path}")
        cases = inventory["cases"]
        selected = next((case for case in cases if isinstance(case, Mapping) and case.get("case_id") == case_id), None)
        if selected is None:
            known = ", ".join(sorted(str(case.get("case_id")) for case in cases if isinstance(case, Mapping) and case.get("case_id"))) or "<none>"
            raise ValueError(f"unknown public satellite case_id {case_id!r}; known: {known}")
        asset_path = selected.get("asset_path") if isinstance(selected, Mapping) else None
        if not isinstance(asset_path, str) or not asset_path.strip():
            raise ValueError(f"public satellite case {case_id!r} has no asset_path")
        case_asset_path = REFERENCE_ROOT / asset_path
        if not case_asset_path.is_file():
            raise FileNotFoundError(f"public satellite case asset is missing: {case_asset_path}")
        case_asset = json.loads(case_asset_path.read_text(encoding="utf-8"))
        if not isinstance(case_asset, Mapping) or case_asset.get("case_id") != case_id:
            raise ValueError(f"public satellite case asset identity mismatch: {case_asset_path}")
        source_basis = case_asset.get("source_basis") if isinstance(case_asset.get("source_basis"), list) else []
        summary = {
            "status": "pass",
            "capability_id": self.capability_id,
            "backend_type": "report_only",
            "case_id": case_id,
            "reference_case_count": len(cases),
            "source_count": len(source_basis),
            "public_reference_informed": True,
            "flight_validated": False,
            "can_claim_high_fidelity": False,
        }
        rows = ({
            "case_id": case_id,
            "domain": case_asset.get("domain", selected.get("domain")),
            "validation_status": case_asset.get("validation_status", selected.get("validation_status")),
            "claim_level": case_asset.get("claim_level", selected.get("claim_level")),
            "source_count": len(source_basis),
            "flight_validated": False,
            "can_claim_high_fidelity": False,
        },)
        return SimulationResult(
            summary=summary,
            trace_rows=rows,
            labels={"validation_claim": "public_reference_informed_not_flight_validated"},
            metadata={"selected_case": dict(selected), "selected_case_asset": dict(case_asset), "inventory": inventory},
        )

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        payload = json.dumps(dict(spec), indent=2, ensure_ascii=False, sort_keys=False)
        return f'''#!/usr/bin/env python3
"""Generated reference.public_satellite_case.v1 lookup script."""

import json
from sat_sim.adapters.reference_public_satellite_case import PublicSatelliteReferenceCaseAdapter

TASK_SPEC = json.loads({payload!r})


def main() -> int:
    adapter = PublicSatelliteReferenceCaseAdapter()
    issues = adapter.validate(TASK_SPEC)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        print(json.dumps({{"ok": False, "errors": [i.to_dict() for i in errors]}}, indent=2, ensure_ascii=False))
        return 2
    result = adapter.run(TASK_SPEC)
    print(json.dumps({{"ok": True, "summary": result.summary, "metadata": result.metadata}}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

    def output_schema(self, capability: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return {"trace": [], "summary": []}


__all__ = ["PublicSatelliteReferenceCaseAdapter"]
