"""Explicit adapter for ``whole_spacecraft.basic_power_attitude_orbit.v1``.

P8-B composes the existing LEO orbit environment, basic ADCS reaction-wheel
pointing, and basic EPS capabilities into a minimal whole-spacecraft slice.  The
first cross-subsystem coupling is intentionally narrow and deterministic:
reaction-wheel power demand is added to the EPS load profile.  This adapter does
not implement thermal/comm/propulsion and does not call legacy demo runners.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from sat_sim.adapter_base import SimulationResult
from sat_sim.adapters.orbit_environment_leo_simple import OrbitEnvironmentLeoSimpleAdapter
from sat_sim.adapters.subsystem_adcs_basic_rw_pointing import AdcsBasicRwPointingAdapter
from sat_sim.adapters.subsystem_eps_basic import EpsBasicAdapter
from sat_sim.adapters.whole_spacecraft_basic_power_orbit import WholeSpacecraftBasicPowerOrbitAdapter
from sat_sim.capability_composition import composition_metadata
from sat_sim.task_validator import ValidationIssue


class WholeSpacecraftBasicPowerAttitudeOrbitAdapter:
    """Minimal whole-spacecraft power + attitude + orbit composition."""

    capability_id = "whole_spacecraft.basic_power_attitude_orbit.v1"
    child_capabilities = (
        "orbit_environment.leo_simple.v1",
        "subsystem.adcs.basic_rw_pointing.v1",
        "subsystem.eps.basic.v1",
    )

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if spec.get("capability_id") != self.capability_id:
            issues.append(ValidationIssue("error", "$.capability_id", f"must be {self.capability_id!r}", "capability"))
        if spec.get("task_type") != "whole_spacecraft":
            issues.append(ValidationIssue("error", "$.task_type", "P8-B capability requires task_type='whole_spacecraft'", "capability"))
        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        if target.get("level") != "whole_spacecraft" or target.get("name") != "basic_power_attitude_orbit":
            issues.append(ValidationIssue("error", "$.target", "P8-B capability requires target.level='whole_spacecraft' and target.name='basic_power_attitude_orbit'", "capability"))
        mode = str(target.get("mode") or "nominal")
        if mode not in {"nominal", "mixed"}:
            issues.append(ValidationIssue("error", "$.target.mode", "P8-B capability supports nominal/mixed modes", "capability"))

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
        adcs_child = self._build_adcs_spec(spec)
        eps_child = self._build_eps_spec(spec, shadow_profile=[1.0], adcs_power_profile=[0.0])
        for child_issue in OrbitEnvironmentLeoSimpleAdapter().validate(orbit_child):
            issues.append(self._prefix_child_issue(child_issue, "orbit_environment"))
        for child_issue in AdcsBasicRwPointingAdapter().validate(adcs_child):
            issues.append(self._prefix_child_issue(child_issue, "adcs"))
        for child_issue in EpsBasicAdapter().validate(eps_child):
            issues.append(self._prefix_child_issue(child_issue, "eps"))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        orbit_adapter = OrbitEnvironmentLeoSimpleAdapter()
        adcs_adapter = AdcsBasicRwPointingAdapter()
        eps_adapter = EpsBasicAdapter()

        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        mode = str(target.get("mode") or "nominal")
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        duration_s = float(sim.get("duration_s", 900.0))
        sample_s = float(sim.get("sample_s", 10.0))
        task_id = str(spec.get("task_id", "whole_spacecraft_basic_power_attitude_orbit_task"))
        case_id = self._case_id(spec)

        orbit_spec = self._build_orbit_spec(spec)
        adcs_spec = self._build_adcs_spec(spec)
        orbit_result = orbit_adapter.run(orbit_spec)
        adcs_result = adcs_adapter.run(adcs_spec)

        orbit_rows = list(orbit_result.trace_rows)
        adcs_rows = list(adcs_result.trace_rows)
        n_child = min(len(orbit_rows), len(adcs_rows))
        shadow_profile = [float(orbit_rows[i].get("environment.shadow_factor", 1.0)) for i in range(n_child)]
        adcs_power_profile = [float(adcs_rows[i].get("adcs.power.rw_power_w", 0.0)) for i in range(n_child)]

        eps_spec = self._build_eps_spec(spec, shadow_profile=shadow_profile, adcs_power_profile=adcs_power_profile)
        eps_result = eps_adapter.run(eps_spec)
        eps_rows = list(eps_result.trace_rows)
        n = min(n_child, len(eps_rows))

        rows: list[dict[str, Any]] = []
        for idx in range(n):
            o = dict(orbit_rows[idx])
            a = dict(adcs_rows[idx])
            e = dict(eps_rows[idx])
            time_s = float(o.get("time_s", e.get("time_s", a.get("time_s", idx * sample_s))))
            row: dict[str, Any] = {
                "task_id": task_id,
                "case_id": case_id,
                "time_s": time_s,
                "sample_index": idx,
                "target_level": "whole_spacecraft",
                "target_name": "basic_power_attitude_orbit",
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
            for key, value in a.items():
                if key.startswith("adcs."):
                    row[key] = value
            row["spacecraft.attitude.pointing_error_deg"] = float(a.get("adcs.pointing.error_deg", 0.0))
            row["spacecraft.power.adcs_rw_power_w"] = float(a.get("adcs.power.rw_power_w", 0.0))
            row["spacecraft.power.total_requested_power_w"] = float(e.get("eps.loads.requested_power_w", 0.0))
            row["label.pointing_state"] = str(a.get("label.pointing_state", "unknown"))
            row["label.adcs.health_state"] = str(a.get("label.health_state", "unknown"))
            row["label.eps.health_state"] = str(e.get("label.health_state", "unknown"))
            row["label.rw_saturation"] = bool(a.get("label.rw_saturation", False))
            row["label.eclipse_active"] = bool(row.get("environment.eclipse_flag", False))

            if bool(row.get("eps.pdu.load_shed_active", False)):
                health = "power_load_shed"
            elif bool(row.get("label.rw_saturation", False)):
                health = "rw_saturated"
            elif str(row.get("label.pointing_state")) != "acquired":
                health = "attitude_converging"
            elif bool(row.get("label.eclipse_active", False)):
                health = "eclipse"
            else:
                health = str(row.get("label.eps.health_state", "nominal"))
            row["label.health_state"] = health
            rows.append(row)

        qoi: dict[str, Any] = {}
        qoi.update({str(key): value for key, value in self._mapping(orbit_result.summary.get("qoi")).items()})
        qoi.update({str(key): value for key, value in self._mapping(eps_result.summary.get("qoi")).items()})
        adcs_summary = dict(adcs_result.summary)
        soc_values = [float(r["eps.battery.soc"]) for r in rows if "eps.battery.soc" in r]
        margin_values = [float(r["eps.power.margin_w"]) for r in rows if "eps.power.margin_w" in r]
        adcs_power_values = [float(r["spacecraft.power.adcs_rw_power_w"]) for r in rows]
        pointing_values = [float(r["spacecraft.attitude.pointing_error_deg"]) for r in rows]
        eclipse_values = [1.0 if bool(r.get("environment.eclipse_flag", False)) else 0.0 for r in rows]
        if soc_values:
            qoi["spacecraft.power.final_soc"] = soc_values[-1]
            qoi["spacecraft.power.min_soc"] = min(soc_values)
            qoi["spacecraft.power.max_soc"] = max(soc_values)
        if margin_values:
            qoi["spacecraft.power.min_margin_w"] = min(margin_values)
        if adcs_power_values:
            qoi["spacecraft.adcs.max_rw_power_w"] = max(adcs_power_values)
            qoi["spacecraft.adcs.mean_rw_power_w"] = sum(adcs_power_values) / len(adcs_power_values)
            qoi["spacecraft.adcs.rw_energy_wh"] = self._integrate_energy_wh(adcs_power_values, sample_s, duration_s)
        if pointing_values:
            qoi["spacecraft.adcs.initial_pointing_error_deg"] = pointing_values[0]
            qoi["spacecraft.adcs.final_pointing_error_deg"] = pointing_values[-1]
            qoi["spacecraft.adcs.max_pointing_error_deg"] = max(pointing_values)
        qoi["spacecraft.adcs.saturation_count"] = int(adcs_summary.get("qoi.adcs.saturation_count", 0))
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
            "target_name": "basic_power_attitude_orbit",
            "capability_id": self.capability_id,
            "mode": mode,
            "child_capabilities": list(self.child_capabilities),
            "qoi": qoi,
            "events": {
                "load_shed_events": int(eps_events.get("load_shed_events", 0)),
                "pdu_overload_events": int(eps_events.get("pdu_overload_events", 0)),
                "eclipse_samples": int(sum(eclipse_values)),
                "rw_saturation_samples": int(sum(1 for r in rows if bool(r.get("label.rw_saturation", False)))),
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
                    {"capability_id": "subsystem.adcs.basic_rw_pointing.v1", "role": "child", "alias": "adcs"},
                    {"capability_id": "subsystem.eps.basic.v1", "role": "child", "alias": "eps"},
                ],
            },
            "field_mappings": [
                "orbit.environment.shadow_factor -> eps.parameters.shadow_profile",
                "adcs.power.rw_power_w -> eps.parameters.load_profile_w",
            ],
            "scope_exclusions": ["thermal", "comm", "propulsion", "advanced_adcs", "legacy_demo_runner"],
        }
        summary["adapter_metadata"] = metadata
        return SimulationResult(summary=summary, trace_rows=tuple(rows), labels=labels, metadata=metadata)

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        payload = json.dumps(dict(spec), indent=2, ensure_ascii=False, sort_keys=False)
        return f'''#!/usr/bin/env python3
"""Generated whole_spacecraft.basic_power_attitude_orbit.v1 capability script.

This deterministic script executes the explicit WholeSpacecraftBasicPowerAttitudeOrbitAdapter
and does not call legacy demo runner functions or full thermal/comm/propulsion graphs.
"""

import json
from pathlib import Path

from sat_sim.adapters.whole_spacecraft_basic_power_attitude_orbit import WholeSpacecraftBasicPowerAttitudeOrbitAdapter
from sat_sim.dataset_writer import write_task_dataset
from sat_sim.task_compiler import compile_task_spec

TASK_SPEC = json.loads({payload!r})


def main() -> int:
    adapter = WholeSpacecraftBasicPowerAttitudeOrbitAdapter()
    issues = adapter.validate(TASK_SPEC)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise SystemExit("; ".join(f"{{i.path}}: {{i.message}}" for i in errors))
    result = adapter.run(TASK_SPEC)
    compiled = compile_task_spec(TASK_SPEC, validate=True)
    output_root = Path(TASK_SPEC.get("outputs", {{}}).get("output_root", TASK_SPEC.get("task_id", "whole_spacecraft_basic_power_attitude_orbit_output")))
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
        from sat_sim.outputs.standard_fields import WHOLE_SPACECRAFT_POWER_ATTITUDE_ORBIT_TRACE_SCHEMA
        return {"trace": WHOLE_SPACECRAFT_POWER_ATTITUDE_ORBIT_TRACE_SCHEMA}

    @classmethod
    def _build_orbit_spec(cls, spec: Mapping[str, Any]) -> dict[str, Any]:
        child = WholeSpacecraftBasicPowerOrbitAdapter._build_orbit_spec(spec)
        child["task_id"] = f"{spec.get('task_id', 'whole_spacecraft')}_orbit_environment"
        return child

    @classmethod
    def _build_adcs_spec(cls, spec: Mapping[str, Any]) -> dict[str, Any]:
        sim = dict(spec.get("simulation") or {}) if isinstance(spec.get("simulation"), Mapping) else {}
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        spacecraft = spec.get("spacecraft") if isinstance(spec.get("spacecraft"), Mapping) else {}
        sc_adcs = spacecraft.get("adcs") if isinstance(spacecraft.get("adcs"), Mapping) else {}

        def first(*keys: str, default: Any = None) -> Any:
            for key in keys:
                if key in params and params.get(key) is not None:
                    return params.get(key)
                if key in sc_adcs and sc_adcs.get(key) is not None:
                    return sc_adcs.get(key)
            return default

        adcs_params = {
            "spacecraft_inertia_kg_m2": float(first("spacecraft_inertia_kg_m2", "inertia_kg_m2", default=12.0)),
            "num_reaction_wheels": int(first("num_reaction_wheels", default=3)),
            "wheel_inertia_kg_m2": float(first("wheel_inertia_kg_m2", default=0.08)),
            "max_rw_torque_nm": float(first("max_rw_torque_nm", "torque_limit_nm", default=0.03)),
            "max_wheel_speed_rad_s": float(first("max_wheel_speed_rad_s", default=900.0)),
            "initial_wheel_speed_rad_s": float(first("initial_wheel_speed_rad_s", default=80.0)),
            "initial_pointing_error_deg": float(first("initial_pointing_error_deg", "pointing_error_deg", default=8.0)),
            "initial_rate_deg_s": float(first("initial_rate_deg_s", default=0.0)),
            "control_kp_nm_per_rad": float(first("control_kp_nm_per_rad", "control_kp", default=0.08)),
            "control_kd_nm_per_rad_s": float(first("control_kd_nm_per_rad_s", "control_kd", default=0.55)),
            "disturbance_torque_nm": float(first("disturbance_torque_nm", default=0.0)),
            "pointing_requirement_deg": float(first("pointing_requirement_deg", default=1.0)),
            "rw_idle_power_w": float(first("rw_idle_power_w", default=2.0)),
            "rw_power_per_torque_w": float(first("rw_power_per_torque_w", default=80.0)),
        }
        return {
            "schema_version": str(spec.get("schema_version", "0.1.0")),
            "task_id": f"{spec.get('task_id', 'whole_spacecraft')}_adcs",
            "task_type": "subsystem",
            "capability_id": "subsystem.adcs.basic_rw_pointing.v1",
            "target": {"level": "subsystem", "name": "adcs", "mode": "nominal"},
            "simulation": {"duration_s": float(sim.get("duration_s", 900.0)), "sample_s": float(sim.get("sample_s", 10.0)), "backend": str(sim.get("backend", "python"))},
            "parameters": adcs_params,
            "outputs": {"output_root": "datasets/_p8b_adcs_child", "trace_format": "csv", "include_summary": True, "include_trace": True, "include_labels": True, "include_manifest": True},
            "metadata": {"case_id": cls._case_id(spec), "parent_task_id": str(spec.get("task_id", "whole_spacecraft"))},
        }

    @classmethod
    def _build_eps_spec(cls, spec: Mapping[str, Any], *, shadow_profile: Sequence[float] | None = None, adcs_power_profile: Sequence[float] | None = None) -> dict[str, Any]:
        child = WholeSpacecraftBasicPowerOrbitAdapter._build_eps_spec(spec, shadow_profile=shadow_profile)
        child["task_id"] = f"{spec.get('task_id', 'whole_spacecraft')}_eps"
        params = child.setdefault("parameters", {})
        base_bus = max(0.0, float(params.get("bus_load_power_w", 20.0)))
        base_payload = max(0.0, float(params.get("payload_load_power_w", 30.0)))
        base_comm = max(0.0, float(params.get("comm_load_power_w", 0.0)))
        base_heater = max(0.0, float(params.get("heater_load_power_w", 0.0)))
        base_adcs = max(0.0, float(params.get("adcs_load_power_w", 0.0)))
        if adcs_power_profile is not None:
            dynamic = [max(0.0, float(x)) for x in adcs_power_profile]
            mean_dynamic = sum(dynamic) / max(1, len(dynamic))
            params["adcs_load_power_w"] = base_adcs + mean_dynamic
            params["adcs_dynamic_power_profile_w"] = dynamic
            params["load_profile_w"] = [base_bus + base_payload + base_comm + base_heater + base_adcs + p for p in dynamic]
        return child

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


__all__ = ["WholeSpacecraftBasicPowerAttitudeOrbitAdapter"]
