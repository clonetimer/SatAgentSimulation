from __future__ import annotations

import unittest

from components.battery.builder import build_simple_battery
from components.link_budget.builder import build_simple_transmitter
from components.transmitter.builder import compute_transmitter
from components.transmitter.schemas import TransmitterConfig


class ComponentBuilderContractTests(unittest.TestCase):
    def test_native_battery_rejects_unsupported_efficiency_mapping(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not expose charge/discharge efficiency"):
            build_simple_battery(
                "contractBattery",
                capacity_wh=10.0,
                initial_soc=0.5,
                discharge_efficiency=0.9,
                charge_efficiency=0.8,
            )

    def test_simple_transmitter_packet_size_is_applied_explicitly(self) -> None:
        transmitter = build_simple_transmitter(
            "contractTransmitter",
            baud_bps=1000.0,
            packet_size_bits=512,
        )
        self.assertEqual(int(transmitter.packetSize), 512)

    def test_simple_transmitter_rejects_rf_power_on_wrong_native_module(self) -> None:
        with self.assertRaisesRegex(ValueError, "SimpleAntenna/AntennaPower"):
            build_simple_transmitter(
                "contractTransmitterPower",
                baud_bps=1000.0,
                transmit_power_w=5.0,
            )

    def test_zero_power_amplifier_fault_has_finite_saturation_level(self) -> None:
        result = compute_transmitter(
            True,
            requested_rate_bps=500_000.0,
            available_power_w=20.0,
            config=TransmitterConfig(max_tx_power_w=0.0, p_sat_w=0.0),
        )

        self.assertEqual(result.tx_power_w, 0.0)
        self.assertLessEqual(result.saturation_level_dB, 0.0)


if __name__ == "__main__":
    unittest.main()
