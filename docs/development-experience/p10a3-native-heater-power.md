# P10-A3 Native thermal-heater electrical power — development experience

Date: 2026-11-19

Status: COMPLETE — merged in PR #194 as `d40761eb7c1f478301fccd6b510e0136c2e28fd6`; focused and full native Basilisk 2.11.0 acceptance passed.

## What changed

- The active whole-spacecraft native thermal-network path connects each `ThermalHeater.electricalPowerOutMsg` directly to the official `SimpleBattery`.
- Per-heater electrical outputs are recorded separately. The runner sums measured recorder values for `heater_eps_load_w` and heater electrical-energy accounting.
- The reduced legacy thermal branch remains unchanged and continues to use `HeaterPowerFeedback`; it is not reclassified as native.
- Added a paired native heater ON/OFF and battery A/B regression, including non-unity `power_scale`, to focused P10-A, continuous native Basilisk and static compile workflows.

## Why

Inspection of `ThermalHeater` showed it already publishes:
- `heaterOutMsg`: binary actuator state;
- `powerOutMsg`: deposited thermal power after heater efficiency;
- `electricalPowerOutMsg`: actual electrical draw after the runtime `power_scale`.

The old `HeaterPowerFeedback` reconstructed electrical draw from nominal `params.power_w` and the binary state. That could disagree with the heater's own scaled electrical output. Directly connecting `electricalPowerOutMsg` is both simpler and more physically consistent than adding an official fixed-load `SimplePowerSink`, which cannot represent the component's runtime power scaling without another controller.

## Pitfalls and decisions

1. Do not connect `powerOutMsg` to EPS: it is deposited heat after efficiency, not electrical consumption.
2. Do not reconstruct load from `heaterOutMsg` plus nominal rated power: it loses runtime `power_scale`.
3. Use `electricalPowerOutMsg` as the source of truth and keep one recorder per heater so the energy audit remains attributable.
4. Keep the PDU-to-heater control link unchanged. The heater component itself applies the PDU permission and thermostat/forced-state logic before writing its electrical output.
5. Keep the reduced legacy thermal fallback bridge in this slice; it remains explicitly proxy-coupled.
6. The configured heater rating remains an engineering assumption, not calibrated hardware evidence.

## Acceptance evidence — PASS

- Official Basilisk 2.11.0 per-heater on/off and non-unity power-scale tests: PASS.
- Paired battery A/B with the expected summed electrical draw after the actual task scheduling boundary: PASS.
- Focused P10-A: 62 passed; full native whole-spacecraft workflow, static checks and governance CI passed on the accepted head.


## Merge

- PR #194 merged to `main` as `d40761eb7c1f478301fccd6b510e0136c2e28fd6`.
- Evidence artifact: `p10a-native-rw-comm-heater-eps-evidence` (GitHub Actions artifact, run 44561323513).
- Engineering boundary remains: rated heater watts and power scaling are model parameters, not hardware calibration.
