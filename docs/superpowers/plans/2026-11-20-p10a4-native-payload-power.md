# P10-A4: Native Payload electrical-load coupling

Date: 2026-11-20

Status: implementation candidate on `feat/p10a4-native-payload-power-20261120`; same-head official Basilisk 2.11.0 acceptance pending.

## Objective

Remove the project-owned `DataActivityPowerBridge` from the active composite whole-spacecraft Payload path (`build_whole_spacecraft_graph`). The separate `whole_spacecraft.unified_native.v1` runtime still has its own Payload bridge and is explicitly reserved for P10-A4B parity; this slice does not claim global removal across capabilities. Use the real Basilisk `SimpleInstrument.nodeDataOutMsg` and EPS PDU permission to gate an official `SimplePowerSink`, then connect that official power node to both the native `SimpleBattery` and the payload thermal node.

## Model semantics

- The payload electrical profile is a fixed active load: `abs(WholeSpacecraftConfig.payload_power_w)` while the instrument is producing data and the Payload PDU is enabled; otherwise 0 W.
- The prior bridge linearly scaled the fixed active power by `baudRate / nominal_rate_bps`. That rate-proportional law is not retained: there is no evidence that this instrument's DC input power is proportional to output data rate.
- The data-rate telemetry remains authoritative for whether the instrument is active. The adapter translates state only; it does not calculate electrical power.
- This fixed-on profile is an explicit engineering assumption, not a calibrated payload power curve. If later evidence establishes standby/idle or rate-dependent draw, introduce those as measured, explicit operating modes rather than silently restoring a baud-rate proxy.

## Implementation

1. Add `PayloadActivityStatusAdapter` that validates the actual `DataNodeUsageMsg` and EPS PDU status, publishing only a fail-closed `DeviceStatusMsg`.
2. Instantiate an official Basilisk `SimplePowerSink` at the configured payload active power and subscribe its `nodeStatusInMsg` to the adapter.
3. Connect the official `nodePowerOutMsg` to `SimpleBattery` and the payload node's existing thermal power-input bridge.
4. Record actual payload power and activity status. Keep payload data generation, storage, downlink, and mission gating unchanged.
5. Update resource-closure QoI to consume the native power recorder rather than the removed custom bridge trace.
6. Add paired power-on/power-off A/B, PDU load-shed, battery response, thermal-energy accounting, and data-path invariance tests.
7. Include the regression in focused P10-A, full native Basilisk, and static compile CI.

## Acceptance gates

- Official upstream Basilisk 2.11.0, not a mock.
- Actual positive instrument baud rate plus PDU permission enables the power node; zero rate, disabled PDU, invalid or not-ready input disables it.
- Active load equals configured fixed payload power; inactive load is exactly 0 W.
- Native battery responds by the configured load after the real task feedback boundary.
- Thermal payload-node bridge reports the configured efficiency conversion from the actual electrical power message.
- Power-node-on and power-node-off paired runs leave instrument data generation, storage accumulation, RF link budget, and downlink behavior unchanged.
- The low-SOC PDU-shed case produces zero payload generation and zero payload power.
- No claim is made that the fixed active profile is rate-dependent or flight-calibrated.

## Scope boundary

This slice removes the Payload activity-to-power proxy from the composite whole-spacecraft builder. It does not migrate the separate unified-native runtime (P10-A4B), the legacy Comm fallback, or the optional propulsion activity bridge.
