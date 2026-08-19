"""Explicit adapter for ``subsystem.comm.basic_ground_pass.v1``.

P10-A implements a deterministic task-level communication/data capability.  It
uses the existing simple LEO orbit/environment capability for ground-station
visibility and then applies a compact link-budget + data-backlog model.  It is
not a high-fidelity RF propagation model, multi-station scheduler, antenna
pointing model, or Basilisk message graph.
"""
from __future__ import annotations

import json
import math
from typing import Any, Mapping, Sequence

from components.link_budget.builder import LinkBudgetConfig, compute_link_budget
from sat_sim.adapter_base import SimulationResult
from sat_sim.adapter_effects import effect_parameter, runtime_effects
from sat_sim.adapters.orbit_environment_leo_simple import OrbitEnvironmentLeoSimpleAdapter
from sat_sim.task_validator import ValidationIssue


class CommBasicGroundPassAdapter:
    """Production adapter for a minimal ground-pass communication capability."""

    capability_id = "subsystem.comm.basic_ground_pass.v1"

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if spec.get("capability_id") != self.capability_id:
            issues.append(ValidationIssue("error", "$.capability_id", f"must be {self.capability_id!r}", "capability"))
        if spec.get("task_type") != "subsystem":
            issues.append(ValidationIssue("error", "$.task_type", "communication capability requires task_type='subsystem'", "capability"))
        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        if target.get("level") != "subsystem" or target.get("name") not in {"comm_data", "comm", "communication"}:
            issues.append(ValidationIssue("error", "$.target", "communication capability requires target.level='subsystem' and target.name='comm'", "capability"))
        if str(target.get("mode") or "nominal") not in {"nominal", "degradation"}:
            issues.append(ValidationIssue("error", "$.target.mode", "comm basic supports nominal/degradation modes", "capability"))

        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        for key in ("duration_s", "sample_s"):
            value = sim.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
                issues.append(ValidationIssue("error", f"$.simulation.{key}", "must be a positive number", "range"))
        if isinstance(sim.get("duration_s"), (int, float)) and isinstance(sim.get("sample_s"), (int, float)):
            if float(sim["sample_s"]) > float(sim["duration_s"]):
                issues.append(ValidationIssue("warning", "$.simulation.sample_s", "sample_s is larger than duration_s; trace will contain one sample", "range"))

        params = self._params(spec)
        nonnegative = {
            "generated_bps",
            "initial_backlog_bits",
            "storage_capacity_bits",
            "max_downlink_bps",
            "transmitter_power_draw_w",
            "transmitter_standby_power_w",
            "required_ebn0_db",
        }
        positive = {
            "raw_rate_bps",
            "tx_power_w",
            "freq_hz",
            "noise_temp_k",
        }
        numeric = {
            "tx_gain_dbi",
            "rx_gain_dbi",
            "misc_loss_db",
            "downlink_eff",
            "ground_station.latitude_deg",
            "ground_station.longitude_deg",
            "ground_station.altitude_m",
            "ground_station.min_elevation_deg",
            "ground_station.max_range_m",
            "altitude_m",
            "inclination_deg",
            "raan_deg",
            "arg_lat0_deg",
        }
        for key in sorted(nonnegative):
            if key in params and not self._is_number(params[key], nonnegative=True):
                issues.append(ValidationIssue("error", f"$.parameters.{key}", "must be a non-negative number", "range"))
        for key in sorted(positive):
            if key in params and not self._is_number(params[key], positive=True):
                issues.append(ValidationIssue("error", f"$.parameters.{key}", "must be a positive number", "range"))
        for key in sorted(numeric):
            value = self._nested_get(params, key)
            if value is not None and not self._is_number(value):
                issues.append(ValidationIssue("error", f"$.parameters.{key}", "must be a number", "range"))
        if "downlink_eff" in params and not self._is_number(params["downlink_eff"], min_value=0.0, max_value=1.0):
            issues.append(ValidationIssue("error", "$.parameters.downlink_eff", "must be in [0, 1]", "range"))
        if "eps_allows_downlink_profile" in params and not self._is_bool_list(params["eps_allows_downlink_profile"]):
            issues.append(ValidationIssue("error", "$.parameters.eps_allows_downlink_profile", "must be an array of booleans", "type"))
        if "downlink_requested_profile" in params and not self._is_bool_list(params["downlink_requested_profile"]):
            issues.append(ValidationIssue("error", "$.parameters.downlink_requested_profile", "must be an array of booleans", "type"))
        supported_effects = {
            "comm_tx_power_loss_pct",
            "comm_tx_gain_loss_db",
            "comm_misc_loss_increase_db",
            "comm_storage_capacity_loss_pct",
        }
        for i, effect in enumerate(runtime_effects(spec, "degradation")):
            if effect.effect_id not in supported_effects:
                issues.append(ValidationIssue("error", f"$.modifiers.degradations[{i}]", f"unsupported communication degradation effect {effect.effect_id!r}", "capability_degradation"))
            if effect.onset_time_s > 0.0:
                issues.append(ValidationIssue(
                    "error",
                    f"$.modifiers.degradations[{i}].onset_time_s",
                    "subsystem.comm.basic_ground_pass.v1 degradations are build-time effects and must start at 0 s",
                    "capability_degradation_timing",
                ))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        params = self._params(spec)
        metadata = spec.get("metadata") if isinstance(spec.get("metadata"), Mapping) else {}

        duration_s = float(sim.get("duration_s", 5400.0))
        sample_s = float(sim.get("sample_s", 60.0))
        task_id = str(spec.get("task_id", "comm_basic_ground_pass_task"))
        case_id = str(metadata.get("case_id", "case_000"))
        mode = str(target.get("mode") or "nominal")

        generated_bps = max(0.0, float(params.get("generated_bps", 1500.0)))
        initial_backlog = max(0.0, float(params.get("initial_backlog_bits", 0.0)))
        storage_capacity = max(0.0, float(params.get("storage_capacity_bits", 12_000_000.0)))
        tx_power_w = max(1e-12, float(params.get("tx_power_w", 5.0)))
        tx_gain_dbi = float(params.get("tx_gain_dbi", 8.0))
        misc_loss_db = float(params.get("misc_loss_db", 2.0))
        applied_degradations: list[str] = []
        for effect in runtime_effects(spec, "degradation"):
            if effect.effect_id == "comm_tx_power_loss_pct":
                loss_pct = max(0.0, min(100.0, effect_parameter(effect, "value_pct", "loss_pct", default=30.0)))
                tx_power_w *= 1.0 - loss_pct / 100.0
                applied_degradations.append(effect.effect_id)
            elif effect.effect_id == "comm_tx_gain_loss_db":
                tx_gain_dbi -= max(0.0, effect_parameter(effect, "loss_db", default=3.0))
                applied_degradations.append(effect.effect_id)
            elif effect.effect_id == "comm_misc_loss_increase_db":
                misc_loss_db += max(0.0, effect_parameter(effect, "increase_db", default=3.0))
                applied_degradations.append(effect.effect_id)
            elif effect.effect_id == "comm_storage_capacity_loss_pct":
                loss_pct = max(0.0, min(100.0, effect_parameter(effect, "value_pct", "loss_pct", default=25.0)))
                storage_capacity *= 1.0 - loss_pct / 100.0
                applied_degradations.append(effect.effect_id)
        max_downlink_bps = params.get("max_downlink_bps")
        max_downlink = None if max_downlink_bps is None else max(0.0, float(max_downlink_bps))
        required_ebn0_db = float(params.get("required_ebn0_db", 3.0))
        tx_power_draw_w = max(0.0, float(params.get("transmitter_power_draw_w", tx_power_w / max(0.05, float(params.get("tx_efficiency", 0.45))))))
        tx_standby_w = max(0.0, float(params.get("transmitter_standby_power_w", 0.5)))
        eps_profile = self._bool_list(params.get("eps_allows_downlink_profile"))
        downlink_profile = self._bool_list(params.get("downlink_requested_profile"))

        orbit_adapter = OrbitEnvironmentLeoSimpleAdapter()
        orbit_spec = self._build_orbit_spec(spec)
        orbit_result = orbit_adapter.run(orbit_spec)
        orbit_rows = list(orbit_result.trace_rows)
        n_samples = len(orbit_rows)

        link_cfg = LinkBudgetConfig(
            raw_rate_bps=max(1.0, float(params.get("raw_rate_bps", 2_000_000.0))),
            tx_power_w=max(1e-12, tx_power_w),
            tx_gain_dbi=tx_gain_dbi,
            rx_gain_dbi=float(params.get("rx_gain_dbi", 18.0)),
            misc_loss_db=misc_loss_db,
            freq_hz=max(1.0, float(params.get("freq_hz", 2.2e9))),
            noise_temp_k=max(1.0, float(params.get("noise_temp_k", 500.0))),
            downlink_eff=max(0.0, min(1.0, float(params.get("downlink_eff", 0.9)))),
        )

        backlog_bits = initial_backlog
        cumulative_generated = 0.0
        cumulative_downlinked = 0.0
        cumulative_dropped = 0.0
        access_count = 0
        downlink_count = 0
        access_windows = 0
        was_access = False
        max_link_margin_db = -1.0e9
        min_link_margin_db = 1.0e9
        max_downlink_rate = 0.0
        tx_energy_wh = 0.0
        rows: list[dict[str, Any]] = []

        for i, orbit_row in enumerate(orbit_rows):
            time_s = float(orbit_row.get("time_s", i * sample_s))
            next_time_s = float(orbit_rows[i + 1].get("time_s", time_s + sample_s)) if i + 1 < n_samples else duration_s
            dt = max(0.0, min(sample_s, next_time_s - time_s)) if i + 1 < n_samples else 0.0
            has_access = bool(orbit_row.get("ground.has_access", False))
            elevation_deg = float(orbit_row.get("ground.elevation_deg", -90.0))
            range_m = max(1.0, float(orbit_row.get("ground.range_m", 1.0e9)))
            eps_allows = self._profile_bool(eps_profile, i, True)
            requested = self._profile_bool(downlink_profile, i, True)

            if has_access:
                access_count += 1
                if not was_access:
                    access_windows += 1
            was_access = has_access

            link = compute_link_budget(link_cfg, range_m)
            link_margin_db = float(link.ebn0_db - required_ebn0_db)
            min_link_margin_db = min(min_link_margin_db, link_margin_db)
            max_link_margin_db = max(max_link_margin_db, link_margin_db)
            raw_effective_rate = max(0.0, float(link.effective_rate_bps))
            if max_downlink is not None:
                raw_effective_rate = min(raw_effective_rate, max_downlink)
            link_ok = bool(link_margin_db >= 0.0)
            downlink_active = bool(has_access and eps_allows and requested and link_ok)
            downlink_rate_bps = raw_effective_rate if downlink_active else 0.0
            max_downlink_rate = max(max_downlink_rate, downlink_rate_bps)
            if downlink_active:
                downlink_count += 1
            generated_bits = generated_bps * dt
            backlog_before_downlink = backlog_bits + generated_bits
            downlinked_bits = min(backlog_before_downlink, downlink_rate_bps * dt)
            backlog_after_downlink = backlog_before_downlink - downlinked_bits
            dropped_bits = max(0.0, backlog_after_downlink - storage_capacity)
            backlog_bits = backlog_after_downlink - dropped_bits
            cumulative_generated += generated_bits
            cumulative_downlinked += downlinked_bits
            cumulative_dropped += dropped_bits
            power_draw = tx_power_draw_w if downlink_active else tx_standby_w
            tx_energy_wh += power_draw * dt / 3600.0
            if downlink_active:
                comm_state = "downlink"
            elif not has_access:
                comm_state = "no_access"
            elif not eps_allows:
                comm_state = "eps_blocked"
            elif not requested:
                comm_state = "idle"
            elif not link_ok:
                comm_state = "link_margin_negative"
            else:
                comm_state = "idle"
            storage_full = bool(backlog_bits >= storage_capacity and storage_capacity > 0.0)
            degradation_active = bool(applied_degradations)
            health_state = "degraded" if (degradation_active or storage_full or link_margin_db < 0.0 and has_access and requested) else "nominal"

            row = {
                "task_id": task_id,
                "case_id": case_id,
                "time_s": round(time_s, 12),
                "sample_index": i,
                "target_level": "subsystem",
                "target_name": "comm_data",
                "mode": mode,
                "comm.access.in_view_flag": has_access,
                "comm.access.elevation_deg": elevation_deg,
                "comm.access.range_m": range_m,
                "comm.link.margin_db": link_margin_db,
                "comm.link.ebn0_db": float(link.ebn0_db),
                "comm.link.fspl_db": float(link.fspl_db),
                "comm.link.raw_rate_bps": link_cfg.raw_rate_bps,
                "comm.link.effective_rate_bps": raw_effective_rate,
                "comm.link.data_rate_bps": downlink_rate_bps,
                "comm.tx.downlink_active": downlink_active,
                "comm.tx.power_draw_w": power_draw,
                "comm.tx.eps_allows_downlink": eps_allows,
                "comm.tx.downlink_requested": requested,
                "comm.data.generation_rate_bps": generated_bps,
                "comm.data.generated_bits": generated_bits,
                "comm.data.downlinked_bits": downlinked_bits,
                "comm.data.backlog_bits": backlog_bits,
                "comm.data.cumulative_generated_bits": cumulative_generated,
                "comm.data.cumulative_downlinked_bits": cumulative_downlinked,
                "comm.data.cumulative_dropped_bits": cumulative_dropped,
                "comm.data.storage_capacity_bits": storage_capacity,
                "label.comm_state": comm_state,
                "label.comm_storage_full": storage_full,
                "label.degradation_active": degradation_active,
                "label.health_state": health_state,
            }
            rows.append(row)

        final = rows[-1] if rows else {}
        access_fraction = access_count / max(1, len(rows))
        downlink_fraction = downlink_count / max(1, len(rows))
        health_state = "nominal"
        if applied_degradations or cumulative_dropped > 0.0 or bool(final.get("label.comm_storage_full", False)):
            health_state = "degraded"
        labels = {
            "health_state": health_state,
            "comm_state": str(final.get("label.comm_state", "unknown")),
            "access_available": bool(access_count > 0),
            "storage_full": bool(final.get("label.comm_storage_full", False)),
        }
        summary = {
            "adapter": self.__class__.__name__,
            "capability_id": self.capability_id,
            "task_id": task_id,
            "status": "complete",
            "trace_rows": len(rows),
            "mode": mode,
            "qoi.comm.access_fraction": access_fraction,
            "qoi.comm.access_windows": access_windows,
            "qoi.comm.downlink_fraction": downlink_fraction,
            "qoi.comm.max_downlink_rate_bps": max_downlink_rate,
            "qoi.comm.generated_bits": cumulative_generated,
            "qoi.comm.downlinked_bits": cumulative_downlinked,
            "qoi.comm.dropped_bits": cumulative_dropped,
            "qoi.comm.final_backlog_bits": float(final.get("comm.data.backlog_bits", 0.0)),
            "qoi.comm.tx_energy_wh": tx_energy_wh,
            "qoi.comm.min_link_margin_db": min_link_margin_db if rows else 0.0,
            "qoi.comm.max_link_margin_db": max_link_margin_db if rows else 0.0,
            "label.comm_state": labels["comm_state"],
            "label.health_state": health_state,
        }
        metadata_out = {
            "adapter": self.__class__.__name__,
            "capability_id": self.capability_id,
            "dependencies": ["orbit_environment.leo_simple.v1"],
            "model_assumptions": [
                "ground-station access is provided by orbit_environment.leo_simple.v1",
                "link margin uses a compact free-space link-budget approximation",
                "data backlog is a deterministic single-queue model",
                "no high-fidelity RF propagation, multi-station scheduling, antenna pointing, or EPS coupling",
            ],
            "parameters": {
                "generated_bps": generated_bps,
                "storage_capacity_bits": storage_capacity,
                "raw_rate_bps": link_cfg.raw_rate_bps,
                "tx_power_w": link_cfg.tx_power_w,
                "required_ebn0_db": required_ebn0_db,
                "applied_degradations": list(applied_degradations),
            },
        }
        return SimulationResult(summary=summary, trace_rows=tuple(rows), labels=labels, metadata=metadata_out)

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        payload = json.dumps(dict(spec), indent=2, ensure_ascii=False, sort_keys=False)
        return f'''#!/usr/bin/env python3
"""Generated subsystem.comm.basic_ground_pass.v1 capability script.

This deterministic script executes the explicit CommBasicGroundPassAdapter and
does not call legacy demo runner functions.
"""

import json
from pathlib import Path

from sat_sim.adapters.subsystem_comm_basic_ground_pass import CommBasicGroundPassAdapter
from sat_sim.dataset_writer import write_task_dataset
from sat_sim.task_compiler import compile_task_spec

TASK_SPEC = json.loads({payload!r})


def main() -> int:
    adapter = CommBasicGroundPassAdapter()
    issues = adapter.validate(TASK_SPEC)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise SystemExit("; ".join(f"{{i.path}}: {{i.message}}" for i in errors))
    result = adapter.run(TASK_SPEC)
    compiled = compile_task_spec(TASK_SPEC, validate=True)
    output_root = Path(TASK_SPEC.get("outputs", {{}}).get("output_root", TASK_SPEC.get("task_id", "comm_basic_ground_pass_output")))
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
        from sat_sim.outputs.standard_fields import COMM_BASIC_GROUND_PASS_TRACE_SCHEMA
        return {"trace": COMM_BASIC_GROUND_PASS_TRACE_SCHEMA}

    @classmethod
    def _build_orbit_spec(cls, spec: Mapping[str, Any]) -> dict[str, Any]:
        sim = dict(spec.get("simulation") or {}) if isinstance(spec.get("simulation"), Mapping) else {}
        params = cls._params(spec)
        orbit = dict(spec.get("orbit_environment") or {}) if isinstance(spec.get("orbit_environment"), Mapping) else {}
        for key in ("earth_radius_m", "earth_mu_m3_s2", "earth_rotation_rad_s", "altitude_m", "inclination_deg", "raan_deg", "arg_lat0_deg", "sun_vector_n", "magnetic_dipole_axis_n", "magnetic_equator_strength_t"):
            if key in params and key not in orbit:
                orbit[key] = params[key]
        if isinstance(params.get("ground_station"), Mapping):
            orbit["ground_station"] = dict(params["ground_station"])
        return {
            "schema_version": str(spec.get("schema_version", "0.1.0")),
            "task_id": f"{spec.get('task_id', 'comm_basic')}_orbit_environment",
            "task_type": "orbit_environment",
            "capability_id": "orbit_environment.leo_simple.v1",
            "target": {"level": "integrated", "name": "orbit_environment", "mode": "nominal"},
            "simulation": {"duration_s": float(sim.get("duration_s", 5400.0)), "sample_s": float(sim.get("sample_s", 60.0)), "backend": str(sim.get("backend", "python"))},
            "orbit_environment": orbit,
            "parameters": {},
            "outputs": {"output_root": "datasets/_p10a_orbit_child", "trace_format": "csv", "include_summary": True, "include_trace": True, "include_labels": True, "include_manifest": True},
            "metadata": {"case_id": cls._case_id(spec), "parent_task_id": str(spec.get("task_id", "comm_basic"))},
        }

    @staticmethod
    def _params(spec: Mapping[str, Any]) -> Mapping[str, Any]:
        return spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}

    @staticmethod
    def _case_id(spec: Mapping[str, Any]) -> str:
        metadata = spec.get("metadata") if isinstance(spec.get("metadata"), Mapping) else {}
        return str(metadata.get("case_id", "case_000"))

    @staticmethod
    def _is_number(value: Any, *, positive: bool = False, nonnegative: bool = False, min_value: float | None = None, max_value: float | None = None) -> bool:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        v = float(value)
        if positive and v <= 0.0:
            return False
        if nonnegative and v < 0.0:
            return False
        if min_value is not None and v < min_value:
            return False
        if max_value is not None and v > max_value:
            return False
        return True

    @staticmethod
    def _nested_get(mapping: Mapping[str, Any], dotted: str) -> Any:
        cur: Any = mapping
        for part in dotted.split("."):
            if not isinstance(cur, Mapping):
                return None
            cur = cur.get(part)
        return cur

    @staticmethod
    def _is_bool_list(value: Any) -> bool:
        return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) and all(isinstance(item, bool) for item in value)

    @classmethod
    def _bool_list(cls, value: Any) -> list[bool]:
        if not cls._is_bool_list(value):
            return []
        return [bool(item) for item in value]

    @staticmethod
    def _profile_bool(profile: Sequence[bool], index: int, default: bool) -> bool:
        if not profile:
            return bool(default)
        if index < len(profile):
            return bool(profile[index])
        return bool(profile[-1])


__all__ = ["CommBasicGroundPassAdapter"]
