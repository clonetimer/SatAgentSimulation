# P10-A5 Unified-native auxiliary heater — development experience

Date: 2026-11-20

Status: implementation candidate; same-head official Basilisk 2.11.0 acceptance pending.

## What changed

- Target: replace `StatusPowerBridge` for the auxiliary PDU-controlled heater with an official Basilisk `SimplePowerSink`.
- Preserve the official battery and thermal-input connections using the same `PowerNodeUsageMsg`.
- Preserve the `heater_power` recorder and `eps.loads.heater_power_w` trace contract.
- Remove one remaining engineering proxy from the default unified-native runtime; optional propulsion remains separately classified.

## Critical source audit

Do not confuse the auxiliary PDU-controlled heater with the thermal network's battery thermostat:
- The thermostat heater is already a project thermal component with a physical hysteresis model and its own `electricalPowerOutMsg`, directly connected to EPS.
- The auxiliary PDU-controlled heater is a separate fixed load represented by `StatusPowerBridge`, and its power message also feeds the battery thermal node.

Only the second is migrated in P10-A5. Removing the auxiliary load or summing it into the thermostat would change the model rather than remove a proxy.

## Scheduling pitfall

The auxiliary power node is scheduled before the PDU publisher in unified-native. Its output at sample *i* therefore reflects the PDU state from the preceding sample. The health audit and battery A/B must compare the next/preceding sample boundary correctly; same-index comparisons would report a false transition mismatch.

## Acceptance still pending

- Official Basilisk 2.11.0 SimplePowerSink and PDU status wiring.
- Paired 15 W / 0 W battery and thermal A/B.
- PDU-disabled zero-load.
- Thermostat heater electrical output remains independently present and unchanged.
- Source inventory, Run Bundle, event, full native and no-Basilisk CI on the exact PR head.
