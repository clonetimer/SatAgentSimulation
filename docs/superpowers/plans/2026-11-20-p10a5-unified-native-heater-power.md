# P10-A5: Native unified-runtime auxiliary heater power

Date: 2026-11-20

Status: implementation candidate on `feat/p10a5-unified-heater-native-power-20261120`; same-head official Basilisk 2.11.0 acceptance pending.

## Objective

Replace the unified-native runtime's `StatusPowerBridge` for the auxiliary PDU-controlled heater load with an official Basilisk `SimplePowerSink`. Preserve the existing auxiliary-heater electrical and thermal allocation while removing its project-owned status-to-power equation.

## Physical distinction — do not collapse two heaters

The unified-native thermal model contains two distinct heater representations:

1. The thermal network's battery thermostat heater, configured by `battery_heater_power_w` and `battery_heater_setpoint_k`. Its own `ThermalHeater.electricalPowerOutMsg` is already connected directly to `SimpleBattery`, and its heat is applied internally to the battery thermal node.
2. A separate auxiliary PDU-controlled heater load, currently represented by `StatusPowerBridge(active_power_w=heater_power_w)`. Its output is also routed to the battery and to the battery thermal-node input bridge.

P10-A5 migrates only item 2. Do not remove or merge the thermal network's thermostat heater. Do not double-count its electrical output.

## Implementation

1. Instantiate an official Basilisk `SimplePowerSink` with `nodePowerOut=-abs(heater_power_w)`.
2. Subscribe the sink's `nodeStatusInMsg` directly to `pdu.heaterStatusOutMsg`.
3. Connect the official `nodePowerOutMsg` to the official battery and retain the existing thermal-input allocation for the separate auxiliary heater.
4. Preserve the `heater_power` recorder and `eps.loads.heater_power_w` trace field.
5. Replace project-bridge health with a native power-node audit. Because the power node is scheduled before the PDU publisher, compare each power sample to the preceding PDU status sample.
6. Remove `project_heater_status_power_bridge` from the engineering-proxy inventory and update source-boundary/Run Bundle counts.
7. Add paired 15 W / 0 W A/B for battery and thermal response, plus PDU-disabled zero-load acceptance.
8. Keep the separate thermal thermostat heater's direct `electricalPowerOutMsg` battery connection unchanged.

## Acceptance gates

- Official upstream Basilisk 2.11.0, not a mock.
- No `StatusPowerBridge` instance remains for the auxiliary PDU-controlled heater.
- Official SimplePowerSink status input is linked to `pdu.heaterStatusOutMsg`.
- PDU enabled produces the configured auxiliary load; PDU disabled produces 0 W.
- Battery responds by the auxiliary load after the actual scheduler boundary.
- The same power message continues to feed the battery thermal node.
- Paired 15 W / 0 W runs show thermal response in the expected direction.
- The independent thermostat heater continues to publish and debit its own electrical output exactly once.
- Default unified-native engineering-proxy count falls from 1 to 0; optional propulsion remains a separate proxy when enabled.
- Existing whole-spacecraft, thermal, PDU, Run Bundle, event and no-Basilisk tests remain green.

## Model boundary

The auxiliary heater remains a fixed active-mode engineering load gated by PDU permission. This migration changes the power-node implementation, not the heater rating's calibration status.
