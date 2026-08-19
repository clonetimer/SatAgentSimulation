"""Explicit adapter for ``component.data_queue.v1``.

S4 exposes the original ``components.data_queue`` Python model/builder API as an
Agent-facing capability. It does not call ``components.data_queue.runner`` and
it does not introduce higher-fidelity storage physics beyond the existing src
functions.
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


class DataQueueAdapter:
    """Source-native adapter for the lightweight data-queue component."""

    capability_id = "component.data_queue.v1"

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if spec.get("capability_id") != self.capability_id:
            issues.append(ValidationIssue("error", "$.capability_id", f"must be {self.capability_id!r}", "capability"))
        if spec.get("task_type") != "component":
            issues.append(ValidationIssue("error", "$.task_type", "data-queue capability requires task_type='component'", "capability"))
        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        if target.get("level") != "component" or target.get("name") != "data_queue":
            issues.append(ValidationIssue("error", "$.target", "data-queue capability requires target.level='component' and target.name='data_queue'", "capability"))
        mode = str(target.get("mode") or "nominal")
        if mode not in {"nominal", "fault", "degradation"}:
            issues.append(ValidationIssue("error", "$.target.mode", "component.data_queue.v1 supports nominal/fault/degradation", "capability"))
        for index, event in enumerate(_normalized_events(spec, "faults", "fault_type")):
            if _event_name(event, "fault") not in {"overflow", "read_stall", "priority_inversion"}:
                issues.append(ValidationIssue("error", f"$.faults[{index}]", "unsupported data-queue fault effect", "capability_fault"))
        for index, event in enumerate(_normalized_events(spec, "degradations", "degradation_type")):
            if _event_name(event, "degradation") not in {"throughput_decay", "packet_error_growth"}:
                issues.append(ValidationIssue("error", f"$.degradations[{index}]", "unsupported data-queue degradation effect", "capability_degradation"))

        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        for key in ("duration_s", "sample_s"):
            value = sim.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
                issues.append(ValidationIssue("error", f"$.simulation.{key}", "must be a positive number", "range"))
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        for key in ("capacity_bits", "initial_queue_bits", "generated_bps", "downlink_bps"):
            if key in params and (isinstance(params[key], bool) or not isinstance(params[key], (int, float)) or float(params[key]) < 0):
                issues.append(ValidationIssue("error", f"$.parameters.{key}", "must be a non-negative number", "range"))
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
        from components.data_queue.builder import DataQueueState, build_nominal_data_queue_config, step_data_queue

        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        mode = str(target.get("mode") or "nominal")
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        duration_s = float(sim.get("duration_s", 300.0))
        sample_s = float(sim.get("sample_s", 10.0))
        task_id = str(spec.get("task_id", "data_queue_task"))
        case_id = str((spec.get("metadata") or {}).get("case_id", "case_000")) if isinstance(spec.get("metadata"), Mapping) else "case_000"

        n_steps = max(1, int(math.ceil(duration_s / sample_s)))
        capacity_bits = float(params.get("capacity_bits", 1_000_000.0))
        initial_queue_bits = float(params.get("initial_queue_bits", 0.0))
        generated = self._profile(params, "generated_profile_bps", "generated_bps", 1000.0, n_steps)
        downlink = self._profile(params, "downlink_profile_bps", "downlink_bps", 0.0, n_steps)

        base_config = build_nominal_data_queue_config(capacity_bits=capacity_bits)
        state = DataQueueState(queue_bits=initial_queue_bits)
        faults = _normalized_events(spec, "faults", "fault_type")
        degradations = _normalized_events(spec, "degradations", "degradation_type")

        rows: list[dict[str, Any]] = []
        time_s = 0.0
        for i in range(n_steps + 1):
            step_idx = max(0, min(i - 1, n_steps - 1))
            effective_capacity = capacity_bits
            effective_generated = generated[step_idx]
            effective_downlink = downlink[step_idx]
            fault_active = False
            degradation_active = False
            for event in faults:
                if not _event_active(event, time_s):
                    continue
                fault_active = True
                effect = _event_name(event, "fault")
                severity = _event_severity(event)
                if effect == "overflow":
                    effective_capacity *= 1.0 - 0.8 * severity
                elif effect == "read_stall":
                    effective_downlink = 0.0
                else:
                    effective_downlink *= 1.0 - 0.7 * severity
            for event in degradations:
                fraction = _degradation_fraction(event, time_s)
                if fraction <= 0.0:
                    continue
                degradation_active = True
                if _event_name(event, "degradation") == "throughput_decay":
                    effective_downlink *= 1.0 - 0.8 * fraction
                else:
                    effective_capacity *= 1.0 - 0.5 * fraction
            config = replace(base_config, capacity_bits=max(0.0, effective_capacity))
            if i:
                dt = min(sample_s, duration_s - (time_s - sample_s))
                state = step_data_queue(state, config, effective_generated, effective_downlink, dt)
            queue_bits = float(state.queue_bits)
            dropped_bits = float(state.dropped_bits)
            downlinked_bits = float(state.downlinked_bits)
            fill_ratio = queue_bits / effective_capacity if effective_capacity > 0 else 0.0
            full = queue_bits >= 0.999 * effective_capacity if effective_capacity > 0 else False
            rows.append({
                "task_id": task_id,
                "case_id": case_id,
                "time_s": round(float(time_s), 12),
                "sample_index": i,
                "target_level": "component",
                "target_name": "data_queue",
                "mode": mode,
                "comm.data_queue.queue_bits": queue_bits,
                "comm.data_queue.capacity_bits": capacity_bits,
                "comm.data_queue.effective_capacity_bits": effective_capacity,
                "comm.data_queue.fill_ratio": fill_ratio,
                "comm.data_queue.dropped_bits": dropped_bits,
                "comm.data_queue.downlinked_bits": downlinked_bits,
                "comm.data_queue.generated_bps": effective_generated,
                "comm.data_queue.downlink_bps": effective_downlink,
                "label.data_queue_full": full,
                "label.fault_active": fault_active,
                "label.degradation_active": degradation_active,
                "label.health_state": "fault" if fault_active else ("degraded" if degradation_active or full else "nominal"),
            })
            time_s = min(duration_s, time_s + sample_s)

        summary = {
            "task_id": task_id,
            "case_id": case_id,
            "status": "complete",
            "duration_s": duration_s,
            "sample_s": sample_s,
            "target_level": "component",
            "target_name": "data_queue",
            "capability_id": self.capability_id,
            "mode": mode,
            "qoi": {
                "comm.data_queue.initial_queue_bits": rows[0]["comm.data_queue.queue_bits"],
                "comm.data_queue.final_queue_bits": rows[-1]["comm.data_queue.queue_bits"],
                "comm.data_queue.max_queue_bits": max(float(r["comm.data_queue.queue_bits"]) for r in rows),
                "comm.data_queue.cumulative_dropped_bits": rows[-1]["comm.data_queue.dropped_bits"],
                "comm.data_queue.cumulative_downlinked_bits": rows[-1]["comm.data_queue.downlinked_bits"],
                "comm.data_queue.capacity_bits": capacity_bits,
            },
            "events": {"storage_full_sample_count": sum(1 for r in rows if r["label.data_queue_full"])},
            "trace_rows": len(rows),
        }
        labels = {"run_labels": [{"task_id": task_id, "mode": mode, "capability_id": self.capability_id}]}
        return SimulationResult(summary=summary, trace_rows=tuple(rows), labels=labels, metadata={"capability_id": self.capability_id})

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        payload = json.dumps(dict(spec), indent=2, ensure_ascii=False, sort_keys=False)
        return f'''#!/usr/bin/env python3
"""Generated component.data_queue.v1 capability script.

This script executes DataQueueAdapter, which calls components.data_queue.builder
source-native functions and does not use the legacy demo runner.
"""

import json
from pathlib import Path

from sat_sim.adapters.component_data_queue import DataQueueAdapter
from sat_sim.dataset_writer import write_task_dataset
from sat_sim.task_compiler import compile_task_spec

TASK_SPEC = json.loads({payload!r})


def main() -> int:
    adapter = DataQueueAdapter()
    issues = adapter.validate(TASK_SPEC)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise SystemExit("; ".join(f"{{i.path}}: {{i.message}}" for i in errors))
    result = adapter.run(TASK_SPEC)
    compiled = compile_task_spec(TASK_SPEC, validate=True)
    output_root = Path(TASK_SPEC.get("outputs", {{}}).get("output_root", TASK_SPEC.get("task_id", "data_queue_capability_output")))
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
        from sat_sim.outputs.standard_fields import DATA_QUEUE_TRACE_SCHEMA
        return {"trace": DATA_QUEUE_TRACE_SCHEMA}

    @staticmethod
    def _profile(params: Mapping[str, Any], profile_key: str, scalar_key: str, default: float, n_steps: int) -> list[float]:
        if isinstance(params.get(profile_key), list) and params[profile_key]:
            raw = [max(0.0, float(v)) for v in params[profile_key]]
        else:
            raw = [max(0.0, float(params.get(scalar_key, default)))]
        if len(raw) < n_steps:
            raw.extend([raw[-1]] * (n_steps - len(raw)))
        return raw[:n_steps]
