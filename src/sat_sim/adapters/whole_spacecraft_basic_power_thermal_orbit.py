"""Explicit adapter for ``whole_spacecraft.basic_power_thermal_orbit.v1``.

P9-B composes the existing LEO orbit environment, basic EPS, and basic lumped
thermal capabilities into a minimal whole-spacecraft power-thermal-orbit slice.
It intentionally avoids full ADCS, communication, propulsion, high-fidelity
thermal networks, and Basilisk message-graph construction.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from sat_sim.adapter_base import SimulationResult
from sat_sim.adapters.orbit_environment_leo_simple import OrbitEnvironmentLeoSimpleAdapter
from sat_sim.adapters.subsystem_eps_basic import EpsBasicAdapter
from sat_sim.adapters.subsystem_thermal_basic_lumped import ThermalBasicLumpedAdapter
from sat_sim.adapters.whole_spacecraft_basic_power_orbit import WholeSpacecraftBasicPowerOrbitAdapter
from sat_sim.capability_composition import composition_metadata
from sat_sim.task_validator import ValidationIssue


class WholeSpacecraftBasicPowerThermalOrbitAdapter:
    """Minimal whole-spacecraft power + thermal + orbit composition."""

    capability_id = "whole_spacecraft.basic_power_thermal_orbit.v1"
    child_capabilities = (
        "orbit_environment.leo_simple.v1",
        "subsystem.eps.basic.v1",
        "subsystem.thermal.basic_lumped.v1",
    )

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if spec.get("capability_id") != self.capability_id:
            issues.append(ValidationIssue("error", "$.capability_id", f"must be {self.capability_id!r}", "capability"))
        if spec.get("task_type") != "whole_spacecraft":
            issues.append(ValidationIssue("error", "$.task_type", "P9-B capability requires task_type='whole_spacecraft'", "capability"))
        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        if target.get("level") != "whole_spacecraft" or target.get("name") != "basic_power_thermal_orbit":
            issues.append(ValidationIssue("error", "$.target", "P9-B capability requires target.level='whole_spacecraft' and target.name='basic_power_thermal_orbit'", "capability"))
        mode = str(target.get("mode") or "nominal")
        if mode not in {"nominal", "mixed", "degradation"}:
            issues.append(ValidationIssue("error", "$.target.mode", "P9-B capability supports nominal/mixed/degradation modes", "capability"))

        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        for key in ("duration_s", "sample_s"):
            value = sim.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
                issues.append(ValidationIssue("error", f"$.simulation.{key}", "must be a positive number", "range"))
        if isinstance(sim.get("duration_s"), (int, float)) and isinstance(sim.get("sample_s"), (int, float)) and not isinstance(sim.get("duration_s"), bool) and not isinstance(sim.get("sample_s"), bool):
            if float(sim["sample_s"]) > float(sim["duration_s"]):
                issues.append(ValidationIssue("error", "$.simulation.sample_s", "must not exceed duration_s", "time"))

        if not isinstance(spec.get("spacecraft"), Mapping):
            issues.append(ValidationIssue("warning", "$.spacecraft", "no spacecraft block found; adapter defaults will be used", "capability_default"))

        orbit_child = self._build_orbit_spec(spec)
        thermal_child = self._build_thermal_spec(spec, shadow_profile=[1.0], internal_power_profile=[self._nominal_electrical_heat_w(spec)])
        eps_child = self._build_eps_spec(spec, shadow_profile=[1.0], heater_power_profile=[0.0])
        for child_issue in OrbitEnvironmentLeoSimpleAdapter().validate(orbit_child):
            issues.append(self._prefix_child_issue(child_issue, "orbit_environment"))
        for child_issue in ThermalBasicLumpedAdapter().validate(thermal_child):
            issues.append(self._prefix_child_issue(child_issue, "thermal"))
        for child_issue in EpsBasicAdapter().validate(eps_child):
            issues.append(self._prefix_child_issue(child_issue, "eps"))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        orbit_adapter = OrbitEnvironmentLeoSimpleAdapter()
        thermal_adapter = ThermalBasicLumpedAdapter()
        eps_adapter = EpsBasicAdapter()

        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        mode = str(target.get("mode") or "nominal")
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        duration_s = float(sim.get("duration_s", 1800.0))
        sample_s = float(sim.get("sample_s", 60.0))
        task_id = str(spec.get("task_id", "whole_spacecraft_basic_power_thermal_orbit_task"))
        case_id = self._case_id(spec)

        orbit_spec = self._build_orbit_spec(spec)
        orbit_result = orbit_adapter.run(orbit_spec)
        orbit_rows = list(orbit_result.trace_rows)
        shadow_profile = [float(row.get("environment.shadow_factor", 1.0)) for row in orbit_rows]
        internal_power_profile = [self._nominal_electrical_heat_w(spec) for _ in shadow_profile]

        thermal_spec = self._build_thermal_spec(spec, shadow_profile=shadow_profile, internal_power_profile=internal_power_profile)
        thermal_result = thermal_adapter.run(thermal_spec)
        thermal_rows = list(thermal_result.trace_rows)
        heater_power_profile = [float(row.get("thermal.heater.power_w", 0.0)) for row in thermal_rows]

        eps_spec = self._build_eps_spec(spec, shadow_profile=shadow_profile, heater_power_profile=heater_power_profile)
        eps_result = eps_adapter.run(eps_spec)
        eps_rows = list(eps_result.trace_rows)
        n = min(len(orbit_rows), len(thermal_rows), len(eps_rows))

        rows: list[dict[str, Any]] = []
        for idx in range(n):
            o = dict(orbit_rows[idx])
            t = dict(thermal_rows[idx])
            e = dict(eps_rows[idx])
            time_s = float(o.get("time_s", e.get("time_s", t.get("time_s", idx * sample_s))))
            row: dict[str, Any] = {
                "task_id": task_id,
                "case_id": case_id,
                "time_s": time_s,
                "sample_index": idx,
                "target_level": "whole_spacecraft",
                "target_name": "basic_power_thermal_orbit",
                "mode": mode,
                "spacecraft.time_s": time_s,
                "spacecraft.sample_index": idx,
            }
            for key, value in o.items():
                if key.startswith(("orbit.", "environment.", "ground.")):
                    row[key] = value
            for key, value in e.items():
                if key.startswith("eps.") or key == "label.fault_active":
                    row[key] = value
            for key, value in t.items():
                if key.startswith("thermal."):
                    row[key] = value
            # Preserve the orbit environment truth after merging thermal rows,
            # because thermal also emits environment fields derived from the same
            # shadow profile.
            row["environment.shadow_factor"] = float(o.get("environment.shadow_factor", row.get("environment.shadow_factor", 1.0)))
            row["environment.eclipse_flag"] = bool(o.get("environment.eclipse_flag", row.get("environment.eclipse_flag", False)))
            row["spacecraft.power.thermal_heater_power_w"] = float(t.get("thermal.heater.power_w", 0.0))
            row["spacecraft.power.total_requested_power_w"] = float(e.get("eps.loads.requested_power_w", 0.0))
            row["spacecraft.thermal.bus_temp_c"] = float(t.get("thermal.node.bus_temp_c", 0.0))
            row["spacecraft.thermal.battery_temp_c"] = float(t.get("thermal.node.battery_temp_c", 0.0))
            row["label.eps.health_state"] = str(e.get("label.health_state", "unknown"))
            row["label.thermal_state"] = str(t.get("label.thermal_state", "unknown"))
            row["label.thermal_hot_flag"] = bool(t.get("label.thermal_hot_flag", False))
            row["label.thermal_cold_flag"] = bool(t.get("label.thermal_cold_flag", False))
            row["label.eclipse_active"] = bool(row.get("environment.eclipse_flag", False))

            if bool(row.get("eps.pdu.load_shed_active", False)):
                health = "power_load_shed"
            elif row["label.thermal_hot_flag"]:
                health = "thermal_hot"
            elif row["label.thermal_cold_flag"]:
                health = "thermal_cold"
            elif row["label.eclipse_active"]:
                health = "eclipse"
            else:
                health = str(row.get("label.eps.health_state", "nominal"))
            row["label.health_state"] = health
            rows.append(row)

        qoi: dict[str, Any] = {}
        qoi.update({str(key): value for key, value in self._mapping(orbit_result.summary.get("qoi")).items()})
        qoi.update({str(key): value for key, value in self._mapping(eps_result.summary.get("qoi")).items()})
        for summary in (thermal_result.summary, eps_result.summary, orbit_result.summary):
            for key, value in dict(summary).items():
                if str(key).startswith("qoi."):
                    qoi[str(key)] = value

        soc_values = [float(r["eps.battery.soc"]) for r in rows if "eps.battery.soc" in r]
        margin_values = [float(r["eps.power.margin_w"]) for r in rows if "eps.power.margin_w" in r]
        bus_temp_values = [float(r["thermal.node.bus_temp_c"]) for r in rows if "thermal.node.bus_temp_c" in r]
        batt_temp_values = [float(r["thermal.node.battery_temp_c"]) for r in rows if "thermal.node.battery_temp_c" in r]
        heater_values = [float(r["spacecraft.power.thermal_heater_power_w"]) for r in rows]
        eclipse_values = [1.0 if bool(r.get("environment.eclipse_flag", False)) else 0.0 for r in rows]
        if soc_values:
            qoi["spacecraft.power.final_soc"] = soc_values[-1]
            qoi["spacecraft.power.min_soc"] = min(soc_values)
            qoi["spacecraft.power.max_soc"] = max(soc_values)
        if margin_values:
            qoi["spacecraft.power.min_margin_w"] = min(margin_values)
        if bus_temp_values:
            qoi["spacecraft.thermal.final_bus_temp_c"] = bus_temp_values[-1]
            qoi["spacecraft.thermal.min_bus_temp_c"] = min(bus_temp_values)
            qoi["spacecraft.thermal.max_bus_temp_c"] = max(bus_temp_values)
        if batt_temp_values:
            qoi["spacecraft.thermal.final_battery_temp_c"] = batt_temp_values[-1]
            qoi["spacecraft.thermal.min_battery_temp_c"] = min(batt_temp_values)
            qoi["spacecraft.thermal.max_battery_temp_c"] = max(batt_temp_values)
        qoi["spacecraft.thermal.heater_energy_wh"] = self._integrate_energy_wh(heater_values, sample_s, duration_s)
        qoi["spacecraft.environment.eclipse_fraction"] = sum(eclipse_values) / max(1, len(eclipse_values))
        qoi["spacecraft.thermal.hot_count"] = sum(1 for r in rows if bool(r.get("label.thermal_hot_flag", False)))
        qoi["spacecraft.thermal.cold_count"] = sum(1 for r in rows if bool(r.get("label.thermal_cold_flag", False)))

        eps_events = self._mapping(eps_result.summary.get("events"))
        summary = {
            "adapter": self.__class__.__name__,
            "status": "complete",
            "task_id": task_id,
            "capability_id": self.capability_id,
            "mode": mode,
            "child_capabilities": list(self.child_capabilities),
            "qoi": qoi,
            "events": {
                "load_shed_events": int(eps_events.get("load_shed_events", 0)),
                "pdu_overload_events": int(eps_events.get("pdu_overload_events", 0)),
                "eclipse_samples": int(sum(eclipse_values)),
                "thermal_hot_samples": int(qoi["spacecraft.thermal.hot_count"]),
                "thermal_cold_samples": int(qoi["spacecraft.thermal.cold_count"]),
                "heater_active_samples": int(sum(1 for value in heater_values if value > 0.0)),
            },
            "trace_rows": len(rows),
        }
        labels = {
            "run_labels": [{"task_id": task_id, "mode": mode, "capability_id": self.capability_id, "child_capabilities": "|".join(self.child_capabilities)}],
            "fault_labels": [dict(f) for f in spec.get("faults", []) or [] if isinstance(f, Mapping)],
        }
        metadata = {
            "capability_id": self.capability_id,
            "child_capabilities": list(self.child_capabilities),
            "composition": composition_metadata(self.capability_id, capability or {}, task_spec=spec) if capability else {
                "capability_id": self.capability_id,
                "composition_chain": [
                    {"capability_id": self.capability_id, "role": "parent"},
                    {"capability_id": "orbit_environment.leo_simple.v1", "role": "child", "alias": "orbit_environment"},
                    {"capability_id": "subsystem.thermal.basic_lumped.v1", "role": "child", "alias": "thermal"},
                    {"capability_id": "subsystem.eps.basic.v1", "role": "child", "alias": "eps"},
                ],
            },
            "field_mappings": [
                "orbit.environment.shadow_factor -> thermal.parameters.shadow_profile",
                "orbit.environment.shadow_factor -> eps.parameters.shadow_profile",
                "eps nominal electrical load -> thermal.parameters.internal_power_profile_w",
                "thermal.heater.power_w -> eps.parameters.heater_dynamic_power_profile_w",
            ],
            "scope_exclusions": ["adcs", "comm", "propulsion", "advanced_thermal_network", "legacy_demo_runner"],
        }
        summary["adapter_metadata"] = metadata
        return SimulationResult(summary=summary, trace_rows=tuple(rows), labels=labels, metadata=metadata)

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        payload = json.dumps(dict(spec), indent=2, ensure_ascii=False, sort_keys=False)
        return f'''#!/usr/bin/env python3
"""Generated whole_spacecraft.basic_power_thermal_orbit.v1 capability script.

This deterministic script executes the explicit WholeSpacecraftBasicPowerThermalOrbitAdapter
and does not call legacy demo runner functions or full ADCS/comm/propulsion graphs.
"""

import json
from pathlib import Path

from sat_sim.adapters.whole_spacecraft_basic_power_thermal_orbit import WholeSpacecraftBasicPowerThermalOrbitAdapter
from sat_sim.dataset_writer import write_task_dataset
from sat_sim.task_compiler import compile_task_spec

TASK_SPEC = json.loads({payload!r})


def main() -> int:
    adapter = WholeSpacecraftBasicPowerThermalOrbitAdapter()
    issues = adapter.validate(TASK_SPEC)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise SystemExit("; ".join(f"{{i.path}}: {{i.message}}" for i in errors))
    result = adapter.run(TASK_SPEC)
    compiled = compile_task_spec(TASK_SPEC, validate=True)
    output_root = Path(TASK_SPEC.get("outputs", {{}}).get("output_root", TASK_SPEC.get("task_id", "whole_spacecraft_basic_power_thermal_orbit_output")))
    dataset = write_task_dataset(
        output_root=output_root,
        compiled=compiled,
        task_spec=TASK_SPEC,
        summary=result.summary,
        trace_rows=result.trace_rows,
        status="complete",
    )
    print(json.dumps({{"ok": True, "summary": result.summary, "dataset": dataset.to_dict()}}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

    def output_schema(self, capability: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if capability and isinstance(capability.get("outputs"), Mapping):
            return dict(capability["outputs"])
        from sat_sim.outputs.standard_fields import WHOLE_SPACECRAFT_POWER_THERMAL_ORBIT_TRACE_SCHEMA
        return {"trace": WHOLE_SPACECRAFT_POWER_THERMAL_ORBIT_TRACE_SCHEMA}

    @classmethod
    def _build_orbit_spec(cls, spec: Mapping[str, Any]) -> dict[str, Any]:
        child = WholeSpacecraftBasicPowerOrbitAdapter._build_orbit_spec(spec)
        child["task_id"] = f"{spec.get('task_id', 'whole_spacecraft')}_orbit_environment"
        return child

    @classmethod
    def _build_eps_spec(cls, spec: Mapping[str, Any], *, shadow_profile: Sequence[float] | None = None, heater_power_profile: Sequence[float] | None = None) -> dict[str, Any]:
        child = WholeSpacecraftBasicPowerOrbitAdapter._build_eps_spec(spec, shadow_profile=shadow_profile)
        child["task_id"] = f"{spec.get('task_id', 'whole_spacecraft')}_eps"
        params = child.setdefault("parameters", {})
        if heater_power_profile is not None:
            dynamic = [max(0.0, float(x)) for x in heater_power_profile]
            params["heater_dynamic_power_profile_w"] = dynamic
        return child

    @classmethod
    def _build_thermal_spec(cls, spec: Mapping[str, Any], *, shadow_profile: Sequence[float] | None = None, internal_power_profile: Sequence[float] | None = None) -> dict[str, Any]:
        sim = dict(spec.get("simulation") or {}) if isinstance(spec.get("simulation"), Mapping) else {}
        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        parent_mode = str(target.get("mode") or "nominal")
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        spacecraft = spec.get("spacecraft") if isinstance(spec.get("spacecraft"), Mapping) else {}
        sc_thermal = spacecraft.get("thermal") if isinstance(spacecraft.get("thermal"), Mapping) else {}

        def first(*keys: str, default: Any = None) -> Any:
            for key in keys:
                if key in params and params.get(key) is not None:
                    return params.get(key)
                if key in sc_thermal and sc_thermal.get(key) is not None:
                    return sc_thermal.get(key)
            return default

        thermal_params = {
            "initial_bus_temp_c": float(first("initial_bus_temp_c", default=20.0)),
            "initial_battery_temp_c": float(first("initial_battery_temp_c", default=18.0)),
            "bus_thermal_capacity_j_k": float(first("bus_thermal_capacity_j_k", default=18000.0)),
            "battery_thermal_capacity_j_k": float(first("battery_thermal_capacity_j_k", default=9000.0)),
            "internal_power_w": float(first("internal_power_w", default=cls._nominal_electrical_heat_w(spec))),
            "battery_internal_power_w": float(first("battery_internal_power_w", default=3.0)),
            "solar_heat_w": float(first("solar_heat_w", default=45.0)),
            "shadow_factor": float(first("shadow_factor", default=1.0)),
            "sink_temp_c": float(first("sink_temp_c", default=-35.0)),
            "radiator_area_m2": float(first("radiator_area_m2", default=0.35)),
            "radiator_emissivity": float(first("radiator_emissivity", default=0.82)),
            "radiator_degradation_factor": float(first("radiator_degradation_factor", default=1.0)),
            "thermal_conductance_bus_battery_w_k": float(first("thermal_conductance_bus_battery_w_k", default=0.45)),
            "heater_power_w": float(first("heater_power_w", default=20.0)),
            "heater_setpoint_c": float(first("heater_setpoint_c", default=5.0)),
            "heater_deadband_c": float(first("heater_deadband_c", default=2.0)),
            "bus_min_temp_c": float(first("bus_min_temp_c", default=-10.0)),
            "bus_max_temp_c": float(first("bus_max_temp_c", default=45.0)),
            "battery_min_temp_c": float(first("battery_min_temp_c", default=0.0)),
            "battery_max_temp_c": float(first("battery_max_temp_c", default=40.0)),
        }
        if shadow_profile is not None:
            thermal_params["shadow_profile"] = [max(0.0, min(1.0, float(x))) for x in shadow_profile]
        if internal_power_profile is not None:
            thermal_params["internal_power_profile_w"] = [max(0.0, float(x)) for x in internal_power_profile]
        mode = "degradation" if parent_mode == "degradation" else "nominal"
        return {
            "schema_version": str(spec.get("schema_version", "0.1.0")),
            "task_id": f"{spec.get('task_id', 'whole_spacecraft')}_thermal",
            "task_type": "subsystem",
            "capability_id": "subsystem.thermal.basic_lumped.v1",
            "target": {"level": "subsystem", "name": "thermal", "mode": mode},
            "simulation": {"duration_s": float(sim.get("duration_s", 1800.0)), "sample_s": float(sim.get("sample_s", 60.0)), "backend": str(sim.get("backend", "python"))},
            "parameters": thermal_params,
            "outputs": {"output_root": "datasets/_p9b_thermal_child", "trace_format": "csv", "include_summary": True, "include_trace": True, "include_labels": True, "include_manifest": True},
            "metadata": {"case_id": cls._case_id(spec), "parent_task_id": str(spec.get("task_id", "whole_spacecraft"))},
        }

    @classmethod
    def _nominal_electrical_heat_w(cls, spec: Mapping[str, Any]) -> float:
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        spacecraft = spec.get("spacecraft") if isinstance(spec.get("spacecraft"), Mapping) else {}
        sc_eps = spacecraft.get("eps") if isinstance(spacecraft.get("eps"), Mapping) else {}
        eps_params = WholeSpacecraftBasicPowerOrbitAdapter._resolved_eps_parameters(sc_eps, params)
        heat_fraction = max(0.0, float(params.get("electrical_load_heat_fraction", params.get("thermal_load_heat_fraction", 0.9))))
        direct_bias = max(0.0, float(params.get("thermal_internal_power_bias_w", 0.0)))
        base = (
            max(0.0, float(eps_params.get("bus_load_power_w", 20.0)))
            + max(0.0, float(eps_params.get("payload_load_power_w", 30.0)))
            + max(0.0, float(eps_params.get("adcs_load_power_w", 0.0)))
            + max(0.0, float(eps_params.get("comm_load_power_w", 0.0)))
        )
        if "internal_power_w" in params:
            return max(0.0, float(params.get("internal_power_w", 0.0)))
        return direct_bias + base * heat_fraction

    @staticmethod
    def _prefix_child_issue(issue: ValidationIssue, child: str) -> ValidationIssue:
        suffix = issue.path[1:] if issue.path.startswith("$") else "." + issue.path
        return ValidationIssue(issue.severity, f"$.composition.{child}{suffix}", issue.message, issue.code)

    @staticmethod
    def _case_id(spec: Mapping[str, Any]) -> str:
        metadata = spec.get("metadata") if isinstance(spec.get("metadata"), Mapping) else {}
        return str(metadata.get("case_id", "case_000"))

    @staticmethod
    def _mapping(value: Any) -> Mapping[str, Any]:
        return value if isinstance(value, Mapping) else {}

    @staticmethod
    def _integrate_energy_wh(power_values: Sequence[float], sample_s: float, duration_s: float) -> float:
        if not power_values:
            return 0.0
        if len(power_values) == 1:
            return float(power_values[0]) * duration_s / 3600.0
        total = 0.0
        for i, value in enumerate(power_values):
            if i == len(power_values) - 1:
                continue
            total += float(value) * sample_s / 3600.0
        return total


__all__ = ["WholeSpacecraftBasicPowerThermalOrbitAdapter"]
