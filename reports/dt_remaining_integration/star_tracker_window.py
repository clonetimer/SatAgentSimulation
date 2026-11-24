"""Exercise ADCS-03 at the native runtime boundary, with fixed inputs and no API translation."""
import json
import math
from pathlib import Path

from sat_sim.bsk_engine.types import BSKEventSpec
from sat_sim.bsk_engine.unified_native import WHOLE_UNIFIED_CAPABILITY_ID, UnifiedNativeRuntime, UnifiedRuntimeConfig


def run(effect):
    events = () if effect is None else (BSKEventSpec(
        event_id="star_loss", category="fault", effect=effect, target="star_tracker",
        start_s=4.0, end_s=8.0,
    ),)
    config = UnifiedRuntimeConfig(capability_id=WHOLE_UNIFIED_CAPABILITY_ID,
        duration_s=12., step_s=0.5, sample_s=1., adcs_only=False, events=events,
        values={"seed":811})
    return UnifiedNativeRuntime(config).run().trace_rows


def inspect(rows):
    result = []
    for r in rows:
        q = [r[f"adcs.star_tracker.q{i}"] for i in range(4)]
        raw = [r[f"adcs.star_tracker.raw_q{i}"] for i in range(4)]
        result.append({"t":r["time_s"], "valid":r["adcs.star_tracker.valid"],
            "raw_norm":math.hypot(*raw),"output_norm":math.hypot(*q),
            "raw_output_abs_dot":abs(sum(a*b for a,b in zip(q,raw))),
            "fusion_mode":r["adcs.fusion.source_mode"]})
    return result


if __name__ == "__main__":
    import sys
    arms = {key:inspect(run(effect)) for key,effect in [
        ("healthy",None),("dropout","adcs_star_tracker_dropout"),
        ("fov_event","adcs_star_tracker_fov_obstruction")]}
    for key, arm in arms.items():
        assert [r["t"] for r in arm] == list(range(13))
        for r in arm:
            invalid = key != "healthy" and 4 <= r["t"] <= 8
            assert abs(r["raw_norm"]-1)<1e-10
            assert r["valid"] == int(not invalid)
            assert abs(r["output_norm"] - int(not invalid)) < 1e-10
            assert abs(r["raw_output_abs_dot"] - int(not invalid)) < 1e-10
    assert arms["dropout"] == arms["fov_event"]
    Path(sys.argv[1]).write_text(json.dumps(arms,indent=2)+"\n",encoding="utf-8")
