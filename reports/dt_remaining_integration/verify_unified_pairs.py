"""Check aligned samples, independent controls and physical responses for SYS-01."""
import argparse
import json
from math import dist, isclose
from pathlib import Path


def verify(data):
    def pair(left, right, factor):
        a, b = data[left], data[right]
        pa, pb = a["parameters"], b["parameters"]
        assert {k for k in pa.keys() | pb.keys() if pa.get(k) != pb.get(k)} == {factor}
        rows = list(zip(a["rows"], b["rows"], strict=True))
        assert len(rows) == 16 and all(l["time_s"] == r["time_s"] for l, r in rows)
        return rows[1:]

    burn = pair("original", "burn", "propulsion_enabled")
    firing = [b for a, b in burn if b["propulsion.total_thrust_n"] > 0]
    assert [b["time_s"] for b in firing] == [6, 8]
    assert all(b["propulsion.electrical_power_w"] == 40 and b["thermal.propulsion_heat_input_w"] == 34 for b in firing)
    def velocity(row):
        return [row[f"orbit.velocity_m_s_{axis}"] for axis in "xyz"]
    timing = pair("delayed", "burn", "propulsion_burn_start_s")
    assert all(velocity(a) == velocity(b) for a, b in timing if a["time_s"] < 5)
    a, b = next((a, b) for a, b in timing if a["time_s"] == 10)
    delta_v = dist(velocity(a), velocity(b))
    assert delta_v > 0.01

    pdu = pair("lowcontact", "overhead", "initial_soc")
    for a, b in pdu:
        for key in ("eps.pdu.heater_enabled", "eps.pdu.payload_enabled", "eps.pdu.comm_enabled",
                    "payload.active", "comm.tx_enabled"):
            assert a[key] == 0 and b[key] == 1
        for key, watts in (("eps.loads.heater_power_w", 12), ("eps.loads.payload_power_w", 38),
                           ("thermal.payload_heat_input_w", 36.1)):
            assert a[key] == 0 and isclose(b[key], watts)
    rf = pair("offaxis", "overhead", "spacecraft_antenna_orientation_b")
    assert all(a["comm.geometric_access"] == b["comm.geometric_access"] == 1 and
               a["comm.command_permitted"] == b["comm.command_permitted"] == 1 for a, b in rf)
    assert all(a["comm.downlink_bps"] == 0 for a, b in rf)
    received = sum(b["comm.downlink_bps"] > 0 for a, b in rf)
    assert received > 0
    geometry = pair("blocked", "overhead", "initial_true_anomaly_rad")
    assert all(a["comm.geometric_access"] == a["comm.downlink_bps"] == 0 and
               b["comm.geometric_access"] == 1 for a, b in geometry)
    eclipse = pair("contact", "blocked", "initial_true_anomaly_rad")
    assert all(b["orbit.eclipse_factor"] == 1 and b["eps.solar_array_power_w"] > 0 for a, b in eclipse)
    sun = [(a["orbit.eclipse_factor"], a["eps.solar_array_power_w"]) for a, b in eclipse]
    assert sun[0][0] == 1 and sun[-1] == (0, 0)
    assert any(0 < factor < 1 for factor, power in sun)
    assert all((power == 0 if factor == 0 else power > 0) for factor, power in sun)
    normal = pair("illum", "blocked", "solar_normal_b_x")
    assert all(a["orbit.eclipse_factor"] == b["orbit.eclipse_factor"] == 1 and
               a["eps.solar_array_power_w"] == 0 and b["eps.solar_array_power_w"] > 0 for a, b in normal)
    thermal = pair("coolcontact", "hotcontact", "initial_payload_temp_k")
    assert all(a["thermal.safe"] == a["payload.active"] == a["comm.active"] == 1 for a, b in thermal)
    inhibited = [b for a, b in thermal if b["thermal.safe"] == 0]
    assert inhibited and all(b["payload.active"] == b["comm.active"] == b["comm.downlink_bps"] == 0 for b in inhibited)
    return dict(post_start_samples=len(pdu), firing_samples_s=[b["time_s"] for b in firing],
                velocity_difference_at_10s_m_s=delta_v, positive_downlink_samples=received,
                peak_downlink_bps=max(b["comm.downlink_bps"] for a, b in rf),
                illuminated_power_w=[min(b["eps.solar_array_power_w"] for a, b in normal),
                                     max(b["eps.solar_array_power_w"] for a, b in normal)],
                hot_inhibited_samples_s=[b["time_s"] for b in inhibited])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(json.loads(args.report.read_bytes())), indent=2))
