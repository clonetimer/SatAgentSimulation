"""Reconstruct stored energy step by step from sealed battery telemetry."""
import argparse
import hashlib
import json
import math
from pathlib import Path


def read_run(root):
    def read(name):
        return json.loads((root/name).read_bytes())
    manifest_hash = read("SEALED.json")["bundle_manifest_sha256"]
    assert hashlib.sha256((root/"bundle_manifest.json").read_bytes()).hexdigest() == manifest_hash
    trace = (root/"results/telemetry.jsonl").read_bytes()
    assert hashlib.sha256(trace).hexdigest() == read("bundle_manifest.json")["files"]["results/telemetry.jsonl"]["sha256"]
    return list(map(json.loads, trace.splitlines())), manifest_hash


def balance(rows):
    clipped, rejected, supplied, residuals = [], [], [], []
    for previous, current in zip(rows, rows[1:]):
        dt = current["time_s"] - previous["time_s"]
        assert abs(dt - .25) < 1e-12
        energy = previous["eps.battery_energy_j"]
        limit = current["eps.battery_capacity_j"]
        incoming = current["eps.net_power_w"] * dt
        assert incoming >= 0  # All samples in these three illuminated cases are charging.
        removed = max(energy-limit, 0.)
        available = energy-removed+incoming
        surplus = max(available-limit, 0.)
        residuals.append(current["eps.battery_energy_j"] - (available-surplus))
        clipped.append(removed)
        rejected.append(surplus)
        supplied.append(incoming)
    assert max(map(abs, residuals)) < 1e-7
    return dict(steps=len(residuals), net_electrical_input_j=math.fsum(supplied),
                capacity_clipped_j=math.fsum(clipped), unstored_input_j=math.fsum(rejected),
                max_abs_step_residual_j=max(map(abs,residuals)),
                final_energy_j=rows[-1]["eps.battery_energy_j"])


def audit(root):
    summary, traces = {}, {}
    for name in ("healthy", "capacity75", "capacity50"):
        rows, identity = read_run(root/name)
        traces[name] = rows
        summary[name] = dict(manifest_sha256=identity, **balance(rows))
    healthy, fault = traces["healthy"], traces["capacity50"]
    deltas = []
    for a,b in zip(healthy[1:],fault[1:],strict=True):
        assert a["time_s"] == b["time_s"]
        delta = b["eps.net_power_w"]-a["eps.net_power_w"]
        assert abs(delta + b["eps.loads.payload_power_w"] - a["eps.loads.payload_power_w"]) < 1e-9
        if abs(delta) > 1e-9:
            deltas.append(dict(time_s=b["time_s"], net_power_increase_w=delta))
    difference = summary["healthy"]["final_energy_j"]-summary["capacity50"]["final_energy_j"]
    lost = summary["capacity50"]
    saved = math.fsum(x["net_power_increase_w"]*.25 for x in deltas)
    assert abs(difference-(lost["capacity_clipped_j"]+lost["unstored_input_j"]-saved)) < 1e-7
    return dict(runs=summary, payload_power_change_samples=deltas, payload_energy_saved_j=saved,
                final_energy_gap_j=difference)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("bundles", type=Path)
    p.add_argument("output", type=Path)
    args = p.parse_args()
    args.output.write_text(json.dumps(audit(args.bundles), indent=2)+"\n", encoding="utf-8")
