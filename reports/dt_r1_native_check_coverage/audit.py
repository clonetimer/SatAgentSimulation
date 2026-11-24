"""Recompute four coverage diagnostics from one sealed four-effect Run."""
import hashlib
import json
import math
import sys
from pathlib import Path

from sat_sim.run_bundle import verify_run_bundle


def audit(bundle):
    assert verify_run_bundle(bundle)["ok"]
    trace = (bundle / "results/telemetry.jsonl").read_bytes()
    rows = [json.loads(line) for line in trace.splitlines()]
    def read(name):
        return json.loads((bundle / name).read_bytes())
    physical = read("validation/validation_outcome.json")["physical_validation"]
    gravity = read("results/summary.json")["orbital_environment"]["gravity_source"]
    keys = ("orbit_altitude_sanity", "quaternion_norm", "fuel_monotonicity", "data_conservation")
    samples = []
    for r in rows:
        assert r["attitude.truth.valid"] == 1
        radius = math.hypot(*(r[f"orbit.position_m_{axis}"] for axis in "xyz"))
        assert abs(radius - r["orbit.radius_m"]) < 1e-7
        samples.append(dict(time_s=r["time_s"], spherical_height_m=radius-gravity["reference_radius_m"],
                            quaternion_norm=math.hypot(*(r[f"attitude.truth.quaternion_BN_q{i}"] for i in range(4))),
                            storage_bits=r["data.storage_bits"], generated_sample_bits=r["payload.generated_data_bits"],
                            downlinked_sample_bits=r["comm.downlinked_bits"]))
    return dict(trace_sha256=hashlib.sha256(trace).hexdigest(), samples=samples,
                reference_radius_m=gravity["reference_radius_m"], gravity_sha256=gravity["sha256"],
                propulsion_enabled=read("input/task_spec.json")["parameters"]["values"]["propulsion_enabled"],
                checks={key:physical["check_results"][key] for key in keys})


if __name__ == "__main__":
    print(json.dumps(audit(Path(sys.argv[1])), indent=2))
