# P10-A4B Unified-native Payload power — development experience

Date: 2026-11-20

Status: implementation candidate; same-head official Basilisk 2.11.0 acceptance pending.

## What changed

- Replaced the unified-native `DataActivityPowerBridge` with the shared `PayloadActivityStatusAdapter` and an official Basilisk `SimplePowerSink`.
- The adapter reads actual `SimpleInstrument.nodeDataOutMsg` and EPS PDU Payload status; it publishes a fail-closed DeviceStatus message and owns no power equation.
- The official SimplePowerSink feeds the battery and the payload thermal-node bridge.
- Unified-native recorder key `payload_power` and trace field `eps.loads.payload_power_w` remain unchanged.
- Payload proxy count is expected to fall from two to one; the auxiliary heater remains an explicit proxy.
- Added a paired 38 W / 0 W test for battery response, thermal response, unchanged data/storage, and PDU shedding.

## Scheduling decision

The instrument is inserted at task priority 40, the PDU at 49, and the mission mode gate at 45. The new status adapter is inserted at priority 39 and the power sink at 38, so the adapter consumes the current tick's instrument data and PDU permission after those publishers have executed. The battery runs earlier at priority 50 and therefore consumes the power-node message on the next tick. Paired battery A/B assertions must compare the following battery sample, not the same-index sample.

## Engineering model boundary

The active payload load is fixed at `payload_power_w`; it is not linearly scaled with baud rate. This matches P10-A4A and avoids retaining an unsupported rate-to-DC-power equation. It is not a calibrated hardware curve.

## Pitfalls to verify

1. Do not connect a second project-owned power equation; the adapter is status-only.
2. Keep the official power node's same output connected to EPS and thermal to avoid split-source accounting.
3. Update runtime manifest and source-boundary counts; otherwise the old proxy may be gone from the graph but still falsely advertised in the audit.
4. Keep the low-SOC PDU transition test: the PDU permit and instrument data rate are independent conditions.
5. The fixed load is a mode assumption. If future hardware evidence supports rate-dependent DC draw, add an explicit measured profile instead of restoring the old proportional proxy.

## Acceptance still pending

- Official Basilisk 2.11.0 unified-native A/B.
- Resource-closure health, PDU transition and event regressions.
- Full native Basilisk, static and governance CI on the exact PR head.
