"""Compare native cumulative counters at sparse output epochs to the full-rate audit."""
import csv
import json
import sys
from pathlib import Path

from run_cases import run


def compare(case, root):
    with (Path(__file__).parent / f"{case}.csv").open(encoding="utf-8", newline="") as stream:
        expected = list(csv.DictReader(stream))
    bundle = root / case
    rows = [json.loads(line) for line in (bundle / "results/telemetry.jsonl").read_bytes().splitlines()]
    assert len(rows) == 4 and "data.max_abs_storage_balance_error_bits" not in rows[0]
    for index, row in enumerate(rows):
        steps = expected[:index * 10]
        if steps:
            assert abs(float(steps[-1]["time_s"]) - row["time_s"]) < 1e-9
            assert row["data.max_abs_storage_balance_error_bits"] == 0
        for key in ("offered_bits", "admitted_bits", "withdrawn_bits"):
            assert row["data.cumulative_" + key] == sum(float(item[key]) for item in steps)
    check = json.loads((bundle / "validation/validation_outcome.json").read_bytes())
    measured = check["physical_validation"]["check_results"]["data_conservation"]
    assert measured["status"] == "PASS" and measured["valid_row_count"] == 3
    return {"times_s": [r["time_s"] for r in rows], "check": measured,
            "counters": [{k:v for k,v in r.items() if k.startswith("data.cumulative_")} for r in rows]}


if __name__ == "__main__":
    source, root = map(Path, sys.argv[1:])
    evidence = {}
    for name, capacity in (("full-rate", None), ("limited-storage", 11000000), ("active-link", None)):
        run(source, root, name, capacity, coarse=True)
        evidence[name] = compare(name, root)
    (root / "comparison.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
