"""Adapter for ``subsystem.thermal_reduced_order.v1``.

THERM-1 upgrades the thermal path from a two-node basic lumped proxy to a
reduced-order node network.  It remains a local physics proxy and is not a
Basilisk-native or flight-correlated thermal solver.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from sat_sim.adapter_base import SimulationResult
from sat_sim.coupled.orbit_attitude_thermal import build_orbit_attitude_thermal_environment
from sat_sim.task_validator import ValidationIssue
from subsystems.thermal.network import (
    THERMAL_NETWORK_SCHEMA_VERSION,
    ThermalNetworkConfig,
    ThermalNetworkError,
    build_thermal_network_payload,
    propagate_thermal_network,
)
from subsystems.thermal.templates import (
    THERMAL_TEMPLATE_SCHEMA_VERSION,
    ThermalTemplateError,
    expand_task_spec_with_thermal_template,
    thermal_template_inventory,
)


class ThermalReducedOrderAdapter:
    """Reduced-order thermal network capability adapter."""

    capability_id = "subsystem.thermal_reduced_order.v1"

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if spec.get("capability_id") != self.capability_id:
            issues.append(ValidationIssue("error", "$.capability_id", f"must be {self.capability_id!r}", "capability"))
        if spec.get("task_type") != "subsystem":
            issues.append(ValidationIssue("error", "$.task_type", "thermal reduced-order capability requires task_type='subsystem'", "capability"))
        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        if target:
            if target.get("level") not in {None, "subsystem"}:
                issues.append(ValidationIssue("error", "$.target.level", "requires target.level='subsystem'", "capability"))
            if target.get("name") not in {None, "thermal", "thermal_reduced_order", "thermal_network"}:
                issues.append(ValidationIssue("error", "$.target.name", "must target thermal / thermal_network", "capability"))
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        for key in ("duration_s", "sample_s"):
            value = sim.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0.0:
                issues.append(ValidationIssue("error", f"$.simulation.{key}", "must be a positive number", "range"))
        try:
            cfg = ThermalNetworkConfig.from_task_spec(self._with_coupled_environment(spec))
            cfg.validate()
        except ThermalNetworkError as exc:
            issues.append(ValidationIssue("error", "$.parameters", str(exc), "capability"))
        except ThermalTemplateError as exc:
            issues.append(ValidationIssue("error", "$.parameters.template_id", str(exc), "template"))
        except Exception as exc:
            issues.append(ValidationIssue("error", "$.parameters", str(exc), "capability"))
        return tuple(issues)

    def _with_coupled_environment(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        out = expand_task_spec_with_thermal_template(spec)
        params = dict(out.get("parameters") if isinstance(out.get("parameters"), Mapping) else {})
        generated_env = build_orbit_attitude_thermal_environment(out)
        if "environment" not in params:
            params["environment"] = generated_env
        else:
            env = dict(params.get("environment") if isinstance(params.get("environment"), Mapping) else {})
            # Preserve template/user flux values but back-fill face exposure maps
            # from the orbit-attitude coupling helper when they are omitted.
            for key in ("face_solar_exposure", "face_albedo_exposure", "face_earth_ir_exposure", "schema_version", "attitude_mode", "altitude_m"):
                env.setdefault(key, generated_env.get(key))
            params["environment"] = env
        out["parameters"] = params
        return out

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        cfg = ThermalNetworkConfig.from_task_spec(self._with_coupled_environment(spec))
        summary, rows = propagate_thermal_network(cfg)
        labels = {
            "thermal_state": summary.get("label.thermal_state"),
            "thermal_hot": bool(summary.get("qoi.thermal.hot_count")),
            "thermal_cold": bool(summary.get("qoi.thermal.cold_count")),
            "validation_claim": "public_reference_informed_not_flight_validated",
        }
        metadata = {
            "schema_version": THERMAL_NETWORK_SCHEMA_VERSION,
            "adapter": self.__class__.__name__,
            "capability_id": self.capability_id,
            "config": cfg.to_dict(),
            "payload": build_thermal_network_payload(self._with_coupled_environment(spec)),
            "template": {
                "schema_version": THERMAL_TEMPLATE_SCHEMA_VERSION,
                "template_id": self._with_coupled_environment(spec).get("parameters", {}).get("template_id"),
                "template_inventory": thermal_template_inventory(),
            },
            "boundary": {
                "backend_type": "local_physics_proxy",
                "basilisk_required": False,
                "thermal_desktop_equivalent": False,
                "flight_validated": False,
            },
        }
        return SimulationResult(summary=summary, trace_rows=rows, labels=labels, metadata=metadata)

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        payload = json.dumps(dict(spec), indent=2, ensure_ascii=False, sort_keys=False)
        return f'''#!/usr/bin/env python3
"""Generated subsystem.thermal_reduced_order.v1 capability script.

This is a reduced-order thermal network. It is public-reference-informed and
benchmark-gated, but it is not flight validated and not a Thermal Desktop/ESATAN
replacement.
"""

import json
from sat_sim.adapters.subsystem_thermal_reduced_order import ThermalReducedOrderAdapter

TASK_SPEC = json.loads({payload!r})


def main() -> int:
    adapter = ThermalReducedOrderAdapter()
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
        if isinstance(capability, Mapping) and isinstance(capability.get("outputs"), Mapping):
            return dict(capability["outputs"])
        return {"trace": [], "summary": []}


__all__ = ["ThermalReducedOrderAdapter"]
