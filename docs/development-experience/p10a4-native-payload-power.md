# P10-A4 Native Payload power — development experience

Date: 2026-11-20

Status: implementation candidate; same-head official Basilisk 2.11.0 acceptance pending.

## What changed

- Replaced the active whole-spacecraft Payload `DataActivityPowerBridge` with a status-only `PayloadActivityStatusAdapter` and official Basilisk `SimplePowerSink`.
- The adapter reads the actual `SimpleInstrument.nodeDataOutMsg` and EPS Payload PDU status. It publishes a fail-closed activity status and owns no electrical-power equation.
- The official power node connects to the native battery and the existing payload thermal node heat-input bridge.
- Added independent recorders for payload active status and actual payload power.
- Updated resource-closure QoI to use the native payload power recorder instead of the removed custom bridge trace.
- Added paired native payload power A/B and PDU load-shed tests.

## Engineering decision

The old project bridge used `P = P_active * min(1, baudRate / nominalRate)`. This is an unverified assumption about instrument DC power scaling with data rate. The new model treats `payload_power_w` as a fixed active-mode load while the instrument is actually producing data. This is closer to a conventional mode-based electrical load model and allows the official Basilisk SimplePowerSink to own the load.

This is a deliberate model-semantics change. The active load is unchanged at nominal rate; below nominal rate it is no longer linearly scaled. Do not describe the new profile as rate-dependent or calibrated. If payload hardware measurements later support a rate-dependent curve, implement that as a separately evidenced model.

## Pitfalls and decisions

1. PDU permission alone is insufficient: mission gating can suppress instrument output while the PDU remains enabled. The adapter requires both valid PDU permission and positive actual `baudRate`.
2. The adapter must run after the instrument and mission-gate outputs are updated; same-task insertion order is part of the acceptance.
3. The SimplePowerSink status input gates the fixed load; do not infer load from a UI or PDU state alone.
4. The same official `PowerNodeUsageMsg` feeds EPS and thermal, preventing two independent payload-power estimates.
5. Power-on/off A/B must leave data generation and storage unchanged; otherwise the power-accounting migration has accidentally changed the science-data path.

## Acceptance still pending

- Official Basilisk 2.11.0 active/inactive and PDU-shed tests.
- Paired battery response and thermal efficiency energy accounting.
- Payload data/storage invariance under power-node-only A/B.
- Full native Basilisk, focused P10-A and static checks on the exact PR head.
