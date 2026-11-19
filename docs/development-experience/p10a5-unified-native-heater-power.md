# P10-A5 Unified-native thermal heater power — development experience

Date: 2026-11-20

Status: implementation candidate; same-head official Basilisk 2.11.0 acceptance pending.

## What changed

- Removed the unified-native PDU-status-only `StatusPowerBridge` heater load.
- The official battery now relies only on the actual `ThermalHeater.electricalPowerOutMsg` outputs already published by the thermal network.
- Removed the parallel external heater-power input from the thermal network. The network already deposits heat from each heater's own `powerOutMsg`.
- Changed `eps.loads.heater_power_w` to sum actual per-node electrical recorder values; per-node `eps.loads.<node>_thermostat_power_w` fields remain available.
- Added a native heater health audit checking total-vs-component consistency and zero load while the PDU is disabled.
- Added a focused official-Basilisk contract for electrical draw, deposited heat, efficiency, power scaling, and PDU fail-closed behavior.
- Default unified-native proxy inventory is expected to fall from one to zero; propulsion remains an optional proxy when enabled.

## Engineering decision

A PDU status bit is not a sufficient measurement of heater electrical power. It only says the heater branch is permitted. The actual thermal heater owns thermostat hysteresis, forced-state faults, and power scaling. Therefore the battery must consume the heater's electrical output, while the thermal node consumes its deposited-heat output. Do not reconstruct either value from PDU state.

The electrical output is `P_e = P_rated × power_scale` when the thermostat is on and the PDU permits it; deposited heat is `Q = efficiency × P_e`. These are separate physical quantities and must not be interchanged.

## Pitfalls

1. The old status bridge was connected to both battery and thermal inputs even though the real thermal heater outputs were also connected. This created a second power/heat path that ignored thermostat state.
2. Keep the existing thermal network's internal node-local heater aggregation. Adding another external `powerOutMsg` input would double-count deposited heat.
3. Do not remove the per-heater recorder messages: they are the evidence used to derive the total load and support per-node diagnosis.
4. The unified-native capability currently does not declare heater stuck-on/off events. This slice tests the heater component's actual PDU/thermostat/power-scale contract directly rather than claiming unsupported unified-native event coverage.
5. The remaining propulsion activity bridge is separate and stays in scope for a later power-authority decision.

## Acceptance still pending

- Official Basilisk 2.11.0 focused heater and P10-A regressions.
- Full native Basilisk, static, unit-only import and governance CI on the exact PR head.
- Whole-spacecraft event and sealed Run Bundle proxy inventory regressions.
