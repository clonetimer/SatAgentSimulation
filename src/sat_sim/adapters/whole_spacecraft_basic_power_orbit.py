"""Explicit adapter for ``whole_spacecraft.basic_power_orbit.v1``.

P7-A defines a minimal whole-spacecraft power-orbit capability by composing the
existing ``orbit_environment.leo_simple.v1`` and ``subsystem.eps.basic.v1``
adapters.  It intentionally does not build a full ADCS/thermal/comm/propulsion
spacecraft graph and does not call any legacy demo runner functions.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from sat_sim.adapter_base import SimulationResult
from sat_sim.capability_composition import composition_metadata
from sat_sim.adapters.orbit_environment_leo_simple import OrbitEnvironmentLeoSimpleAdapter
from sat_sim.adapters.subsystem_eps_basic import EpsBasicAdapter
from sat_sim.task_validator import ValidationIssue


class WholeSpacecraftBasicPowerOrbitAdapter:
    """Production adapter for the minimal whole-spacecraft power-orbit slice."""

    capability_id = "whole_spacecraft.basic_power_orbit.v1"
    child_capabilities = ("orbit_environment.leo_simple.v1", "subsystem.eps.basic.v1")

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if spec.get("capability_id") != self.capability_id:
            issues.append(ValidationIssue("error", "$.capability_id", f"must be {self.capability_id!r}", "capability"))
        if spec.get("task_type") != "whole_spacecraft":
            issues.append(ValidationIssue("error", "$.task_type", "P7-A capability requires task_type='whole_spacecraft'", "capability"))
        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        if target.get("level") != "whole_spacecraft" or target.get("name") != "basic_power_orbit":
            issues.append(ValidationIssue("error", "$.target", "P7-A capability requires target.level='whole_spacecraft' and target.name='basic_power_orbit'", "capability"))
        mode = str(target.get("mode") or "nominal")
        if mode not in {"nominal", "fault", "degradation", "mixed"}:
            issues.append(ValidationIssue("error", "$.target.mode", "P7-A capability supports nominal/fault/degradation/mixed", "capability"))

        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        for key in ("duration_s", "sample_s"):
            value = sim.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
                issues.append(ValidationIssue("error", f"$.simulation.{key}", "must be a positive number", "range"))
        if isinstance(sim.get("duration_s"), (int, float)) and isinstance(sim.get("sample_s"), (int, float)) and not isinstance(sim.get("duration_s"), bool) and not isinstance(sim.get("sample_s"), bool):
            if float(sim["sample_s"]) > float(sim["duration_s"]):
                issues.append(ValidationIssue("error", "$.simulation.sample_s", "must not exceed duration_s", "time"))

        spacecraft = spec.get("spacecraft") if isinstance(spec.get("spacecraft"), Mapping) else {}
        eps = spacecraft.get("eps") if isinstance(spacecraft.get("eps"), Mapping) else {}
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        if not spacecraft:
            issues.append(ValidationIssue("error", "$.spacecraft", "whole-spacecraft power-orbit capability requires a spacecraft object", "required"))
        if not eps and not any(key in params for key in ("battery_capacity_wh", "initial_soc", "solar_array_max_power_w", "solar_power_w")):
            issues.append(ValidationIssue("warning", "$.spacecraft.eps", "no spacecraft.eps block found; adapter defaults will be used", "capability_default"))

        for key, path in (
            ("battery_capacity_wh", "$.spacecraft.eps.battery_capacity_wh"),
            ("solar_power_w", "$.spacecraft.eps.solar_power_w"),
            ("bus_power_w", "$.spacecraft.eps.bus_power_w"),
            ("payload_power_w", "$.spacecraft.eps.payload_power_w"),
        ):
            if key in eps:
                value = eps.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) < 0.0:
                    issues.append(ValidationIssue("error", path, "must be a non-negative number", "range"))
        if "battery_capacity_wh" in eps and isinstance(eps.get("battery_capacity_wh"), (int, float)) and not isinstance(eps.get("battery_capacity_wh"), bool) and float(eps["battery_capacity_wh"]) <= 0:
            issues.append(ValidationIssue("error", "$.spacecraft.eps.battery_capacity_wh", "must be positive", "range"))
        if "initial_soc" in eps:
            value = eps.get("initial_soc")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
                issues.append(ValidationIssue("error", "$.spacecraft.eps.initial_soc", "must be within [0, 1]", "range"))

        # Reuse child capability validators against normalized child specs.  Only
        # keep child errors; warnings remain useful but should not hide the parent path.
        orbit_child = self._build_orbit_spec(spec)
        eps_child = self._build_eps_spec(spec, shadow_profile=[1.0])
        for child_issue in OrbitEnvironmentLeoSimpleAdapter().validate(orbit_child):
            issues.append(self._prefix_child_issue(child_issue, "orbit_environment"))
        for child_issue in EpsBasicAdapter().validate(eps_child):
            issues.append(self._prefix_child_issue(child_issue, "eps"))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        orbit_adapter = OrbitEnvironmentLeoSimpleAdapter()
        eps_adapter = EpsBasicAdapter()

        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        mode = str(target.get("mode") or "nominal")
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        duration_s = float(sim.get("duration_s", 5400.0))
        sample_s = float(sim.get("sample_s", 60.0))
        task_id = str(spec.get("task_id", "whole_spacecraft_basic_power_orbit_task"))
        case_id = self._case_id(spec)

        orbit_spec = self._build_orbit_spec(spec)
        orbit_result = orbit_adapter.run(orbit_spec)
        shadow_profile = [float(row.get("environment.shadow_factor", 1.0)) for row in orbit_result.trace_rows]
        eps_spec = self._build_eps_spec(spec, shadow_profile=shadow_profile)
        eps_result = eps_adapter.run(eps_spec)

        orbit_rows = list(orbit_result.trace_rows)
        eps_rows = list(eps_result.trace_rows)
        n = min(len(orbit_rows), len(eps_rows))
        rows: list[dict[str, Any]] = []
        for idx in range(n):
            o = dict(orbit_rows[idx])
            e = dict(eps_rows[idx])
            time_s = float(o.get("time_s", e.get("time_s", idx * sample_s)))
            row: dict[str, Any] = {
                "task_id": task_id,
                "case_id": case_id,
                "time_s": time_s,
                "sample_index": idx,
                "target_level": "whole_spacecraft",
                "target_name": "basic_power_orbit",
                "mode": mode,
                "spacecraft.time_s": time_s,
                "spacecraft.sample_index": idx,
            }
            for key in (
                "orbit.r_bn_n_m_x", "orbit.r_bn_n_m_y", "orbit.r_bn_n_m_z",
                "orbit.v_bn_n_m_s_x", "orbit.v_bn_n_m_s_y", "orbit.v_bn_n_m_s_z",
                "orbit.radius_m",
                "environment.sun_vector_n_x", "environment.sun_vector_n_y", "environment.sun_vector_n_z",
                "environment.shadow_factor", "environment.eclipse_flag",
                "environment.magnetic_field_n_t_x", "environment.magnetic_field_n_t_y", "environment.magnetic_field_n_t_z",
                "environment.magnetic_field_norm_t",
                "ground.range_m", "ground.elevation_deg", "ground.has_access",
            ):
                if key in o:
                    row[key] = o[key]
            for key in (
                "eps.battery.soc", "eps.battery.storage_wh", "eps.battery.capacity_wh", "eps.battery.effective_capacity_wh", "eps.battery.net_power_w",
                "eps.solar.array_power_w", "eps.solar.panel_count", "eps.solar.incidence_cos", "eps.solar.shadow_factor", "eps.solar.fault_multiplier",
                "eps.solar.normal_b_x", "eps.solar.normal_b_y", "eps.solar.normal_b_z",
                "eps.loads.requested_power_w", "eps.loads.served_power_w", "eps.loads.unserved_power_w",
                "eps.loads.requested.bus_w", "eps.loads.requested.payload_w", "eps.loads.requested.adcs_w", "eps.loads.requested.comm_w", "eps.loads.requested.heater_w",
                "eps.loads.served.bus_w", "eps.loads.served.payload_w", "eps.loads.served.adcs_w", "eps.loads.served.comm_w", "eps.loads.served.heater_w",
                "eps.power.margin_w", "eps.pdu.efficiency", "eps.pdu.bus_max_w", "eps.pdu.load_shed_active", "eps.pdu.shed_loads", "eps.pdu.shed_reason", "eps.pdu.overload_remaining",
                "label.fault_active",
            ):
                if key in e:
                    row[key] = e[key]
            # Prefer the orbit environment shadow flag as the whole-spacecraft
            # environment truth; keep EPS shadow separately for power calculations.
            eclipse_active = bool(row.get("environment.eclipse_flag", False))
            eps_health = str(e.get("label.health_state", "nominal"))
            if row.get("label.fault_active"):
                health = "fault_active"
            elif row.get("eps.pdu.load_shed_active"):
                health = "load_shed"
            elif eclipse_active:
                health = "eclipse"
            else:
                health = eps_health
            row["label.health_state"] = health
            row["label.eclipse_active"] = eclipse_active
            rows.append(row)

        soc_values = [float(r["eps.battery.soc"]) for r in rows if "eps.battery.soc" in r]
        solar_values = [float(r["eps.solar.array_power_w"]) for r in rows if "eps.solar.array_power_w" in r]
        eclipse_values = [1.0 if bool(r.get("environment.eclipse_flag", False)) else 0.0 for r in rows]
        margin_values = [float(r["eps.power.margin_w"]) for r in rows if "eps.power.margin_w" in r]
        qoi: dict[str, Any] = {}
        qoi.update({str(key): value for key, value in self._mapping(orbit_result.summary.get("qoi")).items()})
        qoi.update({str(key): value for key, value in self._mapping(eps_result.summary.get("qoi")).items()})
        if soc_values:
            qoi["spacecraft.power.final_soc"] = soc_values[-1]
            qoi["spacecraft.power.min_soc"] = min(soc_values)
            qoi["spacecraft.power.max_soc"] = max(soc_values)
        if solar_values:
            qoi["spacecraft.power.min_solar_array_power_w"] = min(solar_values)
            qoi["spacecraft.power.max_solar_array_power_w"] = max(solar_values)
        if margin_values:
            qoi["spacecraft.power.min_margin_w"] = min(margin_values)
        qoi["spacecraft.environment.eclipse_fraction"] = sum(eclipse_values) / max(1, len(eclipse_values))
        qoi["spacecraft.trace_rows"] = len(rows)

        eps_events = self._mapping(eps_result.summary.get("events"))
        summary = {
            "task_id": task_id,
            "case_id": case_id,
            "status": "complete",
            "duration_s": duration_s,
            "sample_s": sample_s,
            "target_level": "whole_spacecraft",
            "target_name": "basic_power_orbit",
            "capability_id": self.capability_id,
            "mode": mode,
            "child_capabilities": list(self.child_capabilities),
            "qoi": qoi,
            "events": {
                "fault_count": int(eps_events.get("fault_count", len(spec.get("faults", []) or []))),
                "degradation_count": int(eps_events.get("degradation_count", 1 if spec.get("degradations") else 0)),
                "load_shed_events": int(eps_events.get("load_shed_events", 0)),
                "pdu_overload_events": int(eps_events.get("pdu_overload_events", 0)),
                "eclipse_samples": int(sum(eclipse_values)),
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
                    {"capability_id": "subsystem.eps.basic.v1", "role": "child", "alias": "eps"},
                ],
            },
            "scope_exclusions": ["full_adcs", "thermal", "comm", "propulsion", "legacy_demo_runner"],
        }
        summary["adapter_metadata"] = metadata
        return SimulationResult(summary=summary, trace_rows=tuple(rows), labels=labels, metadata=metadata)

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        payload = json.dumps(dict(spec), indent=2, ensure_ascii=False, sort_keys=False)
        return f'''#!/usr/bin/env python3
"""Generated whole_spacecraft.basic_power_orbit.v1 capability script.

This deterministic script executes the explicit WholeSpacecraftBasicPowerOrbitAdapter
and does not call legacy demo runner functions or full ADCS/thermal/comm/propulsion graphs.
"""

import json
from pathlib import Path

from sat_sim.adapters.whole_spacecraft_basic_power_orbit import WholeSpacecraftBasicPowerOrbitAdapter
from sat_sim.dataset_writer import write_task_dataset
from sat_sim.task_compiler import compile_task_spec

TASK_SPEC = json.loads({payload!r})


def main() -> int:
    adapter = WholeSpacecraftBasicPowerOrbitAdapter()
    issues = adapter.validate(TASK_SPEC)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise SystemExit("; ".join(f"{{i.path}}: {{i.message}}" for i in errors))
    result = adapter.run(TASK_SPEC)
    compiled = compile_task_spec(TASK_SPEC, validate=True)
    output_root = Path(TASK_SPEC.get("outputs", {{}}).get("output_root", TASK_SPEC.get("task_id", "whole_spacecraft_basic_power_orbit_output")))
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
        from sat_sim.outputs.standard_fields import WHOLE_SPACECRAFT_POWER_ORBIT_TRACE_SCHEMA
        return {"trace": WHOLE_SPACECRAFT_POWER_ORBIT_TRACE_SCHEMA}

    @classmethod
    def _build_orbit_spec(cls, spec: Mapping[str, Any]) -> dict[str, Any]:
        sim = dict(spec.get("simulation") or {}) if isinstance(spec.get("simulation"), Mapping) else {}
        orbit = dict(spec.get("orbit_environment") or {}) if isinstance(spec.get("orbit_environment"), Mapping) else {}
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        orbit_parameters: dict[str, Any] = {}
        if isinstance(params.get("ground_station"), Mapping):
            orbit_parameters["ground_station"] = dict(params["ground_station"])
        return {
            "schema_version": str(spec.get("schema_version", "0.1.0")),
            "task_id": f"{spec.get('task_id', 'whole_spacecraft')}_orbit_environment",
            "task_type": "orbit_environment",
            "capability_id": "orbit_environment.leo_simple.v1",
            "target": {"level": "integrated", "name": "orbit_environment", "mode": "nominal"},
            "simulation": {"duration_s": float(sim.get("duration_s", 5400.0)), "sample_s": float(sim.get("sample_s", 60.0)), "backend": str(sim.get("backend", "python"))},
            "orbit_environment": orbit,
            "parameters": orbit_parameters,
            "outputs": {"output_root": "datasets/_p7a_orbit_child", "trace_format": "csv", "include_summary": True, "include_trace": True, "include_labels": True, "include_manifest": True},
            "metadata": {"case_id": cls._case_id(spec), "parent_task_id": str(spec.get("task_id", "whole_spacecraft"))},
        }

    @classmethod
    def _build_eps_spec(cls, spec: Mapping[str, Any], *, shadow_profile: Sequence[float] | None = None) -> dict[str, Any]:
        sim = dict(spec.get("simulation") or {}) if isinstance(spec.get("simulation"), Mapping) else {}
        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        mode = str(target.get("mode") or "nominal")
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        spacecraft = spec.get("spacecraft") if isinstance(spec.get("spacecraft"), Mapping) else {}
        sc_eps = spacecraft.get("eps") if isinstance(spacecraft.get("eps"), Mapping) else {}
        eps_params = cls._resolved_eps_parameters(sc_eps, params)
        if shadow_profile is not None:
            eps_params["shadow_profile"] = [max(0.0, min(1.0, float(x))) for x in shadow_profile]
        return {
            "schema_version": str(spec.get("schema_version", "0.1.0")),
            "task_id": f"{spec.get('task_id', 'whole_spacecraft')}_eps",
            "task_type": "subsystem",
            "capability_id": "subsystem.eps.basic.v1",
            "target": {"level": "subsystem", "name": "eps", "mode": mode},
            "simulation": {"duration_s": float(sim.get("duration_s", 5400.0)), "sample_s": float(sim.get("sample_s", 60.0)), "backend": str(sim.get("backend", "python"))},
            "parameters": eps_params,
            "faults": [dict(f) for f in spec.get("faults", []) or [] if isinstance(f, Mapping)],
            "degradations": dict(spec.get("degradations") or {}) if isinstance(spec.get("degradations"), Mapping) else {},
            "outputs": {"output_root": "datasets/_p7a_eps_child", "trace_format": "csv", "include_summary": True, "include_trace": True, "include_labels": True, "include_manifest": True},
            "metadata": {"case_id": cls._case_id(spec), "parent_task_id": str(spec.get("task_id", "whole_spacecraft"))},
        }

    @staticmethod
    def _resolved_eps_parameters(sc_eps: Mapping[str, Any], params: Mapping[str, Any]) -> dict[str, Any]:
        def first(*keys: str, default: Any = None) -> Any:
            for key in keys:
                if key in params and params.get(key) is not None:
                    return params.get(key)
                if key in sc_eps and sc_eps.get(key) is not None:
                    return sc_eps.get(key)
            return default

        out = {
            "battery_capacity_wh": float(first("battery_capacity_wh", default=160.0)),
            "initial_soc": float(first("initial_soc", default=0.62)),
            "solar_array_max_power_w": float(first("solar_array_max_power_w", "solar_power_w", default=95.0)),
            "solar_array_efficiency": float(first("solar_array_efficiency", default=1.0)),
            "solar_panel_count": int(first("solar_panel_count", "panel_count", default=1)),
            "solar_deployment_fraction": float(first("solar_deployment_fraction", "deployment_fraction", default=1.0)),
            "solar_incidence_cos": float(first("solar_incidence_cos", default=1.0)),
            "enable_solar_tracking": bool(first("enable_solar_tracking", default=False)),
            "solar_tracking_max_slew_rate_rad_s": float(first("solar_tracking_max_slew_rate_rad_s", default=0.0)),
            "bus_load_power_w": float(first("bus_load_power_w", "bus_power_w", default=20.0)),
            "payload_load_power_w": float(first("payload_load_power_w", "payload_power_w", default=30.0)),
            "adcs_load_power_w": float(first("adcs_load_power_w", default=0.0)),
            "comm_load_power_w": float(first("comm_load_power_w", default=0.0)),
            "heater_load_power_w": float(first("heater_load_power_w", default=0.0)),
            "enable_load_shedding": bool(first("enable_load_shedding", default=True)),
            "pdu_bus_max_w": float(first("pdu_bus_max_w", default=90.0)),
            "pdu_efficiency": float(first("pdu_efficiency", default=0.97)),
            "payload_min_soc": float(first("payload_min_soc", "load_shed_soc_threshold", default=0.15)),
            "comm_min_soc": float(first("comm_min_soc", "load_shed_soc_threshold", default=0.15)),
            "heater_min_soc": float(first("heater_min_soc", "load_shed_soc_threshold", default=0.15)),
            "adcs_min_soc": float(first("adcs_min_soc", "load_shed_soc_threshold", default=0.15)),
        }
        for key in ("load_profile_w", "sun_vector_b", "sun_vector_profile_b", "solar_initial_normal_b", "shed_order"):
            if key in params:
                out[key] = params[key]
        return out

    @staticmethod
    def _prefix_child_issue(issue: ValidationIssue, child: str) -> ValidationIssue:
        if issue.severity == "warning":
            return ValidationIssue(issue.severity, f"$.composition.{child}{issue.path[1:] if issue.path.startswith('$') else '.' + issue.path}", issue.message, issue.code)
        return ValidationIssue(issue.severity, f"$.composition.{child}{issue.path[1:] if issue.path.startswith('$') else '.' + issue.path}", issue.message, issue.code)

    @staticmethod
    def _case_id(spec: Mapping[str, Any]) -> str:
        metadata = spec.get("metadata") if isinstance(spec.get("metadata"), Mapping) else {}
        return str(metadata.get("case_id", "case_000"))

    @staticmethod
    def _mapping(value: Any) -> Mapping[str, Any]:
        return value if isinstance(value, Mapping) else {}


__all__ = ["WholeSpacecraftBasicPowerOrbitAdapter"]
