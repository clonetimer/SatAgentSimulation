"""Reconstruct native SimpleStorageUnit balances from full-rate sealed telemetry."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

from sat_sim.run_bundle import verify_run_bundle


def reconstruct(rows, step):
    if not rows or rows[0]["time_s"] != 0:
        raise ValueError("A complete trace starting at t=0 is required")
    level = rows[0]["data.storage_bits"]
    ledger = []
    for prev, row in zip(rows, rows[1:]):
        dt = row["time_s"] - prev["time_s"]
        if not math.isclose(dt, step, rel_tol=0, abs_tol=1e-9):
            raise ValueError("Recorder interval differs from dynamics step")
        source = row["payload.generated_bps"]
        drain = row["data.downlink_node_baud_bps"]
        if not (math.isfinite(source) and math.isfinite(drain) and source >= 0 >= drain):
            raise ValueError("Unexpected node input")
        # C++ round rounds halfway away from zero. The instrument precedes
        # DownlinkHandling in the storage unit's input subscription order.
        offered = math.floor(source * dt + 0.5)
        admitted = offered if level + offered < row["data.storage_capacity_bits"] else 0
        requested = math.floor(-drain * dt + 0.5)
        withdrawn = min(level + admitted, requested)
        level += admitted - withdrawn
        ledger.append(dict(time_s=row["time_s"], offered_bits=offered, admitted_bits=admitted,
                           capacity_rejected_bits=offered-admitted, withdrawn_bits=withdrawn,
                           requested_removal_bits=requested, predicted_bits=level, observed_bits=row["data.storage_bits"],
                           residual_bits=row["data.storage_bits"]-level,
                           node_sum_error_bps=row["data.net_baud_bps"]-(source+drain)))
    if not ledger:
        raise ValueError("No integration interval")
    return ledger


def audit(bundle, output):
    if not verify_run_bundle(bundle)["ok"]:
        raise ValueError("Invalid seal")
    task = json.loads((bundle / "input/task_spec.json").read_bytes())
    raw = (bundle / "results/telemetry.jsonl").read_bytes()
    rows = [json.loads(line) for line in raw.splitlines()]
    if not math.isclose(rows[-1]["time_s"], task["simulation"]["duration_s"], rel_tol=0, abs_tol=1e-9):
        raise ValueError("Incomplete time span")
    ledger = reconstruct(rows, task["simulation"]["step_s"])
    with output.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ledger[0])
        writer.writeheader()
        writer.writerows(ledger)
    print(bundle.name, hashlib.sha256(raw).hexdigest(),
          {k:sum(r[k] for r in ledger) for k in ("offered_bits","capacity_rejected_bits","withdrawn_bits")},
          "max_abs_residual_bits", max(abs(r["residual_bits"]) for r in ledger))
    if any(r["residual_bits"] or abs(r["node_sum_error_bps"]) > 1e-9 for r in ledger):
        raise ValueError("Storage or node-rate discrepancy")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    audit(args.bundle, args.output)
