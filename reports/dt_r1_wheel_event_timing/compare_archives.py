"""Compare physical telemetry only after both browser downloads pass independent audit."""
import json
import math
import sys
from pathlib import Path
from zipfile import ZipFile

from browser_archive import audit

FIELDS = ("adcs.rw.actual_applied_torque_nm_0", "adcs.rw.speed_rad_s_0",
          "attitude.truth.pointing_error_deg", "eps.loads.adcs_power_w", "eps.battery_energy_j")


def extract(path):
    receipt = audit(path)
    with ZipFile(path) as archive:
        prefix = receipt["run_id"] + "/"
        spec = json.loads(archive.read(prefix + "input/task_spec.json"))
        rows = [json.loads(line) for line in archive.read(prefix + "results/telemetry.jsonl").splitlines()]
    return receipt, spec, rows


def compare(left, right):
    a, sa, ra = extract(left)
    b, sb, rb = extract(right)
    same = {key:sa[key] == sb[key] for key in ("model", "simulation", "parameters", "events", "mission")}
    if not all(same.values()):
        raise ValueError(f"Simulation inputs differ: {same}")
    assert [r["time_s"] for r in ra] == [r["time_s"] for r in rb]
    maxima = {}
    for field in FIELDS:
        differences = [abs(x[field]-y[field]) for x,y in zip(ra,rb,strict=True)]
        assert all(map(math.isfinite, differences))
        i = max(range(len(differences)), key=differences.__getitem__)
        maxima[field] = {"max_abs": differences[i], "time_s":ra[i]["time_s"]}
    return {"left":a, "right":b, "same_input_sections":same, "differences":maxima}


if __name__ == "__main__":
    a,b,out = map(Path,sys.argv[1:])
    out.write_text(json.dumps(compare(a,b),indent=2,allow_nan=False)+"\n")
