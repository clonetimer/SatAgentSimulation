# P10-A3 Native thermal-heater power — development experience

Date: 2026-11-19

Status: implementation candidate; same-head official Basilisk 2.11.0 acceptance pending.

## What changed

- The active whole-spacecraft native thermal-network path now constructs one official Basilisk `SimplePowerSink` for each thermal heater.
- Each power sink subscribes directly to the matching thermal heater `heaterOutMsg` through `nodeStatusInMsg`; the official power node itself applies the fixed configured wattage.
- Each output is attached to the official battery and separately recorded. The runner sums measured per-node outputs for `heater_eps_load_w` and heater electrical-energy accounting.
- The reduced legacy thermal branch remains unchanged and continues to use `HeaterPowerFeedback`; it is not reclassified as native.
- Added the P10-A3 regression to focused P10-A, continuous native Basilisk and static compile workflows.

## Why

A thermal heater is a binary fixed-load device in this model. Basilisk `SimplePowerSink` already implements fixed power and optional DeviceStatus gating, so a project-owned aggregate power equation is unnecessary for the native thermal-network path.

## Pitfalls and decisions

1. Preserve one power node per heater rather than aggregating all heaters into one custom message; this keeps each source visible in the runtime manifest and recorder evidence.
2. Subscribe the official sink to the heater's actual output status, not merely the PDU permit. The heater may be off because its thermostat is satisfied even while PDU permission remains enabled.
3. Keep the PDU-to-heater control link unchanged. PDU permission gates the heater actuator; heater output then gates its electrical load.
4. Do not remove the reduced legacy thermal fallback bridge in this slice. It is a separate compatibility path and remains explicitly proxy-coupled.
5. The configured heater wattage remains an assumption. Native power-node provenance does not turn that rating into calibrated hardware evidence.

## Acceptance still pending

- Official Basilisk 2.11.0 per-heater on/off power-node tests.
- Paired battery A/B with the expected summed heater wattage after the actual task scheduling boundary.
- Whole-spacecraft thermal, PDU, battery, Run Bundle, and full native CI regressions on the exact branch head.
