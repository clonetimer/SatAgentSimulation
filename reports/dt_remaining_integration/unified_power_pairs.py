"""Extract sealed SYS-01 telemetry for common-time comparison of one-factor runs."""
import argparse
import hashlib
import json
from pathlib import Path
from sat_sim.run_bundle import verify_run_bundle

FIELDS = [
    "time_s", "eps.pdu.payload_enabled", "eps.pdu.comm_enabled", "eps.pdu.heater_enabled",
    "eps.loads.heater_power_w", "eps.loads.payload_power_w", "payload.active",
    "payload.generated_bps", "comm.active", "comm.geometric_access", "thermal.safe",
    "adcs.pointing_error_deg", "thermal.payload_heat_input_w", "thermal.propulsion_heat_input_w",
    "propulsion.enabled", "propulsion.total_thrust_n", "propulsion.electrical_power_w",
    "comm.command_permitted", "comm.tx_enabled", "comm.attempted_rate_bps", "comm.downlink_bps",
    "comm.native_cnr", "eps.solar_array_power_w", "orbit.eclipse_factor", "thermal.payload_temp_k",
    *(f"orbit.velocity_m_s_{axis}" for axis in "xyz"),
]


def extract(root):
    if not verify_run_bundle(root)["ok"]:
        raise ValueError(f"Failed bundle verification: {root}")
    raw = (root / "results/telemetry.jsonl").read_bytes()
    spec = json.loads((root / "input/task_spec.json").read_bytes())
    rows = [json.loads(line) for line in raw.splitlines()]
    return {
        "validation": json.loads((root / "run_record.json").read_bytes())["validation_result"],
        "telemetry_sha256": hashlib.sha256(raw).hexdigest(),
        "manifest_sha256": hashlib.sha256((root / "bundle_manifest.json").read_bytes()).hexdigest(),
        "parameters": spec["parameters"]["values"],
        "rows": [{key: row.get(key) for key in FIELDS} for row in rows],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("bundles", nargs="+", metavar="CASE=PATH")
    args = parser.parse_args()
    data = {key: extract(Path(path)) for key, path in (item.split("=", 1) for item in args.bundles)}
    ticks = [row["time_s"] for row in data["original"]["rows"]]
    assert all([row["time_s"] for row in case["rows"]] == ticks for case in data.values())
    args.output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf8")
