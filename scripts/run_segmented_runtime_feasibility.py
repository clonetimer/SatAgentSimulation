"""
用途：验证同一 Basilisk foundation 实例分段推进与单段推进的等价性及增量采样行为。
参数：--output 指定机器可读 JSON 报告；默认测试 0.1、0.5、1.0 秒 quantum。
输出：记录各 quantum 耗时、峰值 Python 内存和数值差；全部等价时返回 0，否则返回 1。
"""
from __future__ import annotations

import argparse
import json
import math
import time
import tracemalloc
from pathlib import Path

from sat_sim.bsk_engine.types import BSKEventSpec, BSKScenarioConfig
from sat_sim.bsk_engine.unified_native import WHOLE_UNIFIED_CAPABILITY_ID, UnifiedNativeRuntime, UnifiedRuntimeConfig
from sat_sim.interactive.basilisk_runtime import FoundationBasiliskRuntime
from sat_sim.interactive.unified_runtime import UnifiedPersistentRuntime

ROOT = Path(__file__).resolve().parents[1]


def _run(config: BSKScenarioConfig, stops: list[float]) -> tuple[list[dict], float, int]:
    runtime = FoundationBasiliskRuntime(config)
    tracemalloc.start()
    started = time.perf_counter()
    runtime.prepare()
    instance_id = id(runtime._sim)
    rows = [row for stop in stops for row in runtime.advance_to(stop)]
    same_instance = id(runtime._sim) == instance_id
    runtime.finalize()
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    if not same_instance:
        raise RuntimeError("Basilisk instance changed between segments")
    return rows, elapsed, peak


def _case(quantum_s: float) -> dict:
    duration_s = 4.0
    config = BSKScenarioConfig(
        scenario_id=f"segment-feasibility-{quantum_s}", capability_id="whole_spacecraft.bsksim_foundation.v1",
        duration_s=duration_s, step_s=0.1, sample_s=quantum_s, mode_request="inertialPoint",
        events=(BSKEventSpec(event_id="marker", category="fault", effect="marker", target="spacecraft", start_s=2.0),),
    )
    count = int(round(duration_s / quantum_s))
    stops = [round((idx + 1) * quantum_s, 12) for idx in range(count)]
    segmented, segmented_wall_s, segmented_peak = _run(config, stops)
    single, single_wall_s, single_peak = _run(config, [duration_s])
    max_delta = 0.0
    same = len(segmented) == len(single)
    for left, right in zip(segmented, single, strict=False):
        for key in left.keys() | right.keys():
            if isinstance(left.get(key), (int, float)) and not isinstance(left.get(key), bool):
                max_delta = max(max_delta, abs(float(left[key]) - float(right[key])))
            elif left.get(key) != right.get(key):
                same = False
    unique_times = len({row["time_s"] for row in segmented}) == len(segmented)
    passed = same and unique_times and max_delta <= 1e-10 and any(row["label.fault_active"] for row in segmented)
    return {
        "quantum_s": quantum_s, "status": "PASS" if passed else "FAIL", "rows": len(segmented),
        "unique_monotonic_samples": unique_times, "max_numeric_delta": max_delta,
        "segmented_wall_s": segmented_wall_s, "single_wall_s": single_wall_s,
        "segmented_python_peak_bytes": segmented_peak, "single_python_peak_bytes": single_peak,
    }


def _unified_case(quantum_s: float) -> dict:
    duration_s = 2.0
    event = BSKEventSpec(
        event_id="segment-crossing-event", category="fault", effect="payload_instrument_off",
        target="payload", start_s=0.75, end_s=1.25,
    )
    config = UnifiedRuntimeConfig(
        capability_id=WHOLE_UNIFIED_CAPABILITY_ID, duration_s=duration_s, step_s=0.1,
        sample_s=quantum_s, adcs_only=False, values={}, events=(event,),
    )
    runtime = UnifiedPersistentRuntime(config)
    started = time.perf_counter()
    runtime.prepare()
    worker_ident = runtime.worker_ident
    count = int(round(duration_s / quantum_s))
    rows = tuple(
        row
        for index in range(count)
        for row in runtime.advance_to(round((index + 1) * quantum_s, 12))
    )
    metadata = runtime.finalize()
    segmented_wall_s = time.perf_counter() - started
    single = UnifiedNativeRuntime(config).run()
    max_delta = 0.0
    equivalent = len(rows) == len(single.trace_rows)
    for left, right in zip(rows, single.trace_rows, strict=False):
        for key in left.keys() | right.keys():
            if isinstance(left.get(key), (int, float)) and not isinstance(left.get(key), bool):
                max_delta = max(max_delta, abs(float(left[key]) - float(right[key])))
            elif left.get(key) != right.get(key):
                equivalent = False
    passed = bool(
        equivalent and max_delta <= 1e-10 and runtime.worker_ident == worker_ident
        and metadata["summary"]["execution_segment_count"] == count
        and any(row["label.fault_active"] for row in rows)
        and any(int(row["payload.active"]) == 0 for row in rows if row["label.fault_active"])
    )
    return {
        "quantum_s": quantum_s, "status": "PASS" if passed else "FAIL",
        "rows": len(rows), "same_worker": runtime.worker_ident == worker_ident,
        "max_numeric_delta": max_delta, "event_crossed_segment_boundary": True,
        "segmented_wall_s": segmented_wall_s,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "interactive" / "segmented_runtime_feasibility.json")
    args = parser.parse_args()
    cases = [_case(value) for value in (0.1, 0.5, 1.0)]
    unified_cases = [_unified_case(value) for value in (0.1, 0.5, 1.0)]
    passed = all(case["status"] == "PASS" and math.isfinite(case["segmented_wall_s"]) for case in (*cases, *unified_cases))
    report = {
        "schema_version": "segmented-runtime-feasibility.v1", "status": "PASS" if passed else "FAIL",
        "scope": "Foundation probe and default whole_spacecraft.unified_native.v1 persistent execution.",
        "foundation_cases": cases, "whole_spacecraft_unified_cases": unified_cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
