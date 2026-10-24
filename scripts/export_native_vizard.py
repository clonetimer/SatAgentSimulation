#!/usr/bin/env python3
"""Create an official Basilisk Vizard .bin from a fresh finite native scenario.

用途：仅在本机用真正 Basilisk 原生模块录制 Vizard .bin，不生成伪物理回放。
参数：--output 指定未存在的 .bin 目标；--duration-s 或 --task-spec 指定有限时长整星。
输出：官方 Vizard 二进制文件及含 SHA-256 的 .vizard.json，失败时拒绝写入伪制品。

This is a LOCAL, explicit output command, never exposed as an arbitrary HTTP
filesystem writer. No Unity app, browser embedded player, or replay converter
is used. See docs/guides_zh/16_Vizard原生三维回放与前端集成评估.md.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path

from sat_sim.bsk_engine.unified_native import (
    WHOLE_UNIFIED_CAPABILITY_ID, UnifiedNativeRuntime, _config,
)
from sat_sim.bsk_engine.vizard_native_export import validate_local_vizard_destination


def create_recording(*, output: str | Path, duration_s: float = 120.,
                     task_spec: str | Path | None = None) -> dict:
    output_path = validate_local_vizard_destination(output)
    if task_spec is None:
        spec = {
            "task": {"id": "native_vizard_demo", "name": "Basilisk Vizard playback"},
            "model": {"capability_id": WHOLE_UNIFIED_CAPABILITY_ID},
            "simulation": {
                "level": "whole_spacecraft", "backend": "basilisk",
                "duration_s": duration_s, "step_s": .2, "sample_s": 2.,
            },
            "assurance": {"allow_proxy": False},
            "parameters": {"values": {}},
            "outputs": {"plots": []},
        }
    else:
        spec = json.loads(Path(task_spec).read_text(encoding="utf-8"))
    model = spec.get("model", {})
    sim = spec.get("simulation", {})
    if (model.get("capability_id") != WHOLE_UNIFIED_CAPABILITY_ID
            or sim.get("backend") != "basilisk"
            or sim.get("continuous", False)
            or sim.get("duration_s", 0) <= 0
            or sim.get("duration_s", 0) > 3600):
        raise ValueError("VIZARD_NATIVE_FINITE_WHOLE_BASILISK_ONLY")
    from sat_sim.bsk_engine.unified_native import WholeSpacecraftUnifiedNativeAdapter
    issues = WholeSpacecraftUnifiedNativeAdapter().validate(spec)
    if any(item.severity == "error" for item in issues):
        raise ValueError("VIZARD_TASKSPEC_VALIDATION_FAILED:" + str(issues))
    config = replace(_config(spec, WHOLE_UNIFIED_CAPABILITY_ID, False),
                     vizard_save_file=str(output_path))
    result = UnifiedNativeRuntime(config).run()
    evidence = dict(result.metadata["vizard_native_recording"])
    evidence.update({
        "simulation_duration_s": float(config.duration_s),
        "native_runtime_truth": result.summary["runtime_truth_status"],
        "recorded_trace_rows": len(result.trace_rows),
    })
    manifest_path = output_path.with_suffix(".vizard.json")
    manifest_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description="Export official Basilisk .bin for desktop Vizard")
    parser.add_argument("--output", type=Path, required=True, help="new output .bin path (must not exist)")
    parser.add_argument("--duration-s", type=float, default=120., help="demo duration <= 3600 s")
    parser.add_argument("--task-spec", type=Path, default=None, help="finite Basilisk whole-spacecraft TaskSpec JSON")
    args = parser.parse_args()
    data = create_recording(output=args.output, duration_s=args.duration_s,
                            task_spec=args.task_spec)
    print(json.dumps(data, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
