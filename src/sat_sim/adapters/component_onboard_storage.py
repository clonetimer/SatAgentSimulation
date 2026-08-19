"""Explicit adapter for ``component.onboard_storage.v1``.

S4 exposes the original ``components.onboard_storage`` Python builder API as a
source-native Agent capability. It intentionally avoids the demo runner and any
Basilisk-only message graph.
"""
from __future__ import annotations

import json
import math
from typing import Any, Mapping, Sequence

from sat_sim.adapter_base import SimulationResult
from sat_sim.adapters.component_source_native_misc import (
    _degradation_fraction,
    _event_active,
    _event_name,
    _event_severity,
    _normalized_events,
)
from sat_sim.task_validator import ValidationIssue


class OnboardStorageAdapter:
    """Source-native adapter for standalone onboard storage accounting."""

    capability_id = "component.onboard_storage.v1"

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if spec.get("capability_id") != self.capability_id:
            issues.append(ValidationIssue("error", "$.capability_id", f"must be {self.capability_id!r}", "capability"))
        if spec.get("task_type") != "component":
            issues.append(ValidationIssue("error", "$.task_type", "onboard-storage capability requires task_type='component'", "capability"))
        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        if target.get("level") != "component" or target.get("name") != "onboard_storage":
            issues.append(ValidationIssue("error", "$.target", "onboard-storage capability requires target.level='component' and target.name='onboard_storage'", "capability"))
        mode = str(target.get("mode") or "nominal")
        if mode not in {"nominal", "fault", "degradation"}:
            issues.append(ValidationIssue("error", "$.target.mode", "component.onboard_storage.v1 supports nominal/fault/degradation", "capability"))
        for index, event in enumerate(_normalized_events(spec, "faults", "fault_type")):
            if _event_name(event, "fault") not in {"bad_block", "read_only", "corruption"}:
                issues.append(ValidationIssue("error", f"$.faults[{index}]", "unsupported onboard-storage fault effect", "capability_fault"))
        for index, event in enumerate(_normalized_events(spec, "degradations", "degradation_type")):
            if _event_name(event, "degradation") not in {"capacity_loss", "bit_error_growth"}:
                issues.append(ValidationIssue("error", f"$.degradations[{index}]", "unsupported onboard-storage degradation effect", "capability_degradation"))

        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        for key in ("duration_s", "sample_s"):
            value = sim.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
                issues.append(ValidationIssue("error", f"$.simulation.{key}", "must be a positive number", "range"))
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        for key in ("capacity_bits", "initial_stored_bits", "generated_bps", "downlink_bps"):
            if key in params and (isinstance(params[key], bool) or not isinstance(params[key], (int, float)) or float(params[key]) < 0):
                issues.append(ValidationIssue("error", f"$.parameters.{key}", "must be a non-negative number", "range"))
        if "high_watermark" in params:
            value = params["high_watermark"]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= float(value) <= 1:
                issues.append(ValidationIssue("error", "$.parameters.high_watermark", "must be within [0, 1]", "range"))
        for key in ("generated_profile_bps", "downlink_profile_bps"):
            if key in params:
                value = params[key]
                if not isinstance(value, list) or not value:
                    issues.append(ValidationIssue("error", f"$.parameters.{key}", "must be a non-empty numeric list", "type"))
                elif any(isinstance(v, bool) or not isinstance(v, (int, float)) or float(v) < 0 for v in value):
                    issues.append(ValidationIssue("error", f"$.parameters.{key}", "all values must be non-negative numbers", "range"))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from dataclasses import replace
        from components.onboard_storage.builder import OnboardStorageState, build_nominal_onboard_storage_config, step_onboard_storage

        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        mode = str(target.get("mode") or "nominal")
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        duration_s = float(sim.get("duration_s", 300.0))
        sample_s = float(sim.get("sample_s", 10.0))
        task_id = str(spec.get("task_id", "onboard_storage_task"))
        case_id = str((spec.get("metadata") or {}).get("case_id", "case_000")) if isinstance(spec.get("metadata"), Mapping) else "case_000"
        n_steps = max(1, int(math.ceil(duration_s / sample_s)))

        capacity_bits = float(params.get("capacity_bits", 20_000_000.0))
        high_watermark = float(params.get("high_watermark", 0.9))
        state = OnboardStorageState(stored_bits=float(params.get("initial_stored_bits", 0.0)))
        base_config = build_nominal_onboard_storage_config(capacity_bits=capacity_bits, high_watermark=high_watermark)
        generated_bps = self._profile(params, "generated_profile_bps", "generated_bps", 1000.0, n_steps)
        downlink_bps = self._profile(params, "downlink_profile_bps", "downlink_bps", 0.0, n_steps)
        faults = _normalized_events(spec, "faults", "fault_type")
        degradations = _normalized_events(spec, "degradations", "degradation_type")

        rows: list[dict[str, Any]] = []

        def make_row(time_s: float, sample_index: int, gen_bps: float, dlnk_bps: float, effective_capacity: float, fault_active: bool, degradation_active: bool) -> dict[str, Any]:
            fill_ratio = state.stored_bits / effective_capacity if effective_capacity > 0 else 0.0
            high = fill_ratio >= high_watermark if capacity_bits > 0 else False
            full = state.stored_bits >= 0.999 * effective_capacity if effective_capacity > 0 else False
            health = "storage_full" if full else ("high_watermark" if high else "nominal")
            return {
                "task_id": task_id,
                "case_id": case_id,
                "time_s": round(float(time_s), 12),
                "sample_index": sample_index,
                "target_level": "component",
                "target_name": "onboard_storage",
                "mode": mode,
                "comm.storage.stored_bits": state.stored_bits,
                "comm.storage.capacity_bits": capacity_bits,
                "comm.storage.effective_capacity_bits": effective_capacity,
                "comm.storage.fill_ratio": fill_ratio,
                "comm.storage.overflow_bits": state.overflow_bits,
                "comm.storage.downlinked_bits": state.downlinked_bits,
                "comm.storage.generated_bps": gen_bps,
                "comm.storage.downlink_bps": dlnk_bps,
                "comm.storage.high_watermark": high_watermark,
                "label.storage_high_watermark": high,
                "label.storage_full": full,
                "label.fault_active": fault_active,
                "label.degradation_active": degradation_active,
                "label.health_state": "fault" if fault_active else ("degraded" if degradation_active else health),
            }

        t = 0.0
        for i in range(n_steps + 1):
            idx = min(max(i - 1, 0), n_steps - 1)
            effective_capacity = capacity_bits
            effective_generated = generated_bps[idx]
            effective_downlink = downlink_bps[idx]
            fault_active = False
            degradation_active = False
            for event in faults:
                if not _event_active(event, t):
                    continue
                fault_active = True
                effect = _event_name(event, "fault")
                severity = _event_severity(event)
                if effect == "bad_block":
                    effective_capacity *= 1.0 - 0.8 * severity
                elif effect == "read_only":
                    effective_generated = 0.0
                else:
                    effective_generated *= 1.0 - 0.8 * severity
            for event in degradations:
                fraction = _degradation_fraction(event, t)
                if fraction <= 0.0:
                    continue
                degradation_active = True
                if _event_name(event, "degradation") == "capacity_loss":
                    effective_capacity *= 1.0 - 0.8 * fraction
                else:
                    effective_generated *= 1.0 - 0.5 * fraction
            config = replace(base_config, capacity_bits=max(0.0, effective_capacity))
            if i:
                dt = min(sample_s, duration_s - (t - sample_s))
                state = step_onboard_storage(
                    state,
                    config,
                    generated_bits=effective_generated * dt,
                    downlinked_bits=effective_downlink * dt,
                )
            rows.append(make_row(t, i, effective_generated, effective_downlink, effective_capacity, fault_active, degradation_active))
            if t >= duration_s - 1e-12:
                break
            t = min(duration_s, t + sample_s)

        summary = {
            "task_id": task_id,
            "case_id": case_id,
            "status": "complete",
            "duration_s": duration_s,
            "sample_s": sample_s,
            "target_level": "component",
            "target_name": "onboard_storage",
            "capability_id": self.capability_id,
            "mode": mode,
            "qoi": {
                "comm.storage.initial_stored_bits": rows[0]["comm.storage.stored_bits"],
                "comm.storage.final_stored_bits": rows[-1]["comm.storage.stored_bits"],
                "comm.storage.max_stored_bits": max(float(r["comm.storage.stored_bits"]) for r in rows),
                "comm.storage.cumulative_overflow_bits": rows[-1]["comm.storage.overflow_bits"],
                "comm.storage.cumulative_downlinked_bits": rows[-1]["comm.storage.downlinked_bits"],
                "comm.storage.capacity_bits": capacity_bits,
            },
            "events": {
                "high_watermark_sample_count": sum(1 for r in rows if r["label.storage_high_watermark"]),
                "full_sample_count": sum(1 for r in rows if r["label.storage_full"]),
            },
            "trace_rows": len(rows),
        }
        labels = {"run_labels": [{"task_id": task_id, "mode": mode, "capability_id": self.capability_id}]}
        return SimulationResult(summary=summary, trace_rows=tuple(rows), labels=labels, metadata={"capability_id": self.capability_id})

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        payload = json.dumps(dict(spec), indent=2, ensure_ascii=False, sort_keys=False)
        return f'''#!/usr/bin/env python3
"""Generated component.onboard_storage.v1 capability script.

This script executes OnboardStorageAdapter, which calls components.onboard_storage.builder
source-native functions and does not use the legacy demo runner.
"""

import json
from pathlib import Path

from sat_sim.adapters.component_onboard_storage import OnboardStorageAdapter
from sat_sim.dataset_writer import write_task_dataset
from sat_sim.task_compiler import compile_task_spec

TASK_SPEC = json.loads({payload!r})


def main() -> int:
    adapter = OnboardStorageAdapter()
    issues = adapter.validate(TASK_SPEC)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise SystemExit("; ".join(f"{{i.path}}: {{i.message}}" for i in errors))
    result = adapter.run(TASK_SPEC)
    compiled = compile_task_spec(TASK_SPEC, validate=True)
    output_root = Path(TASK_SPEC.get("outputs", {{}}).get("output_root", TASK_SPEC.get("task_id", "onboard_storage_capability_output")))
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
        from sat_sim.outputs.standard_fields import ONBOARD_STORAGE_TRACE_SCHEMA
        return {"trace": ONBOARD_STORAGE_TRACE_SCHEMA}

    @staticmethod
    def _profile(params: Mapping[str, Any], profile_key: str, scalar_key: str, default: float, n_steps: int) -> list[float]:
        if isinstance(params.get(profile_key), list) and params[profile_key]:
            raw = [max(0.0, float(v)) for v in params[profile_key]]
        else:
            raw = [max(0.0, float(params.get(scalar_key, default)))]
        if len(raw) < n_steps:
            raw.extend([raw[-1]] * (n_steps - len(raw)))
        return raw[:n_steps]
