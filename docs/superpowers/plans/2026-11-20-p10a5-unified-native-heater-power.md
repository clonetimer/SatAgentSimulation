# P10-A5: Unified-native thermal-heater electrical-load authority

Date: 2026-11-20

Status: MERGED — PR #198 merged to `main` as `8b269e12dc7ea9652f30bdd6e6a93fd01166996c` (2026-11-20); focused official Basilisk 2.11.0 and independent physical/Run Bundle acceptance passed.

## Objective

Remove the last default engineering power proxy from `whole_spacecraft.unified_native.v1` and make the actual project thermal heaters the sole authority for heater electrical consumption. Preserve the thermal heater's separate electrical-consumption and deposited-heat outputs.

## Finding

The unified-native runtime previously connected both:
- a PDU-status-only `StatusPowerBridge` with a fixed 15 W load to the battery and thermal input; and
- each actual `ThermalHeater.electricalPowerOutMsg` to the battery, while the thermal network also consumed each heater's actual deposited-heat `powerOutMsg`.

The fixed PDU proxy did not follow thermostat hysteresis or actual `power_scale`. It could therefore add a parallel load when the real heater was off or double-count active heater consumption. The existing thermal heater models already publish the correct electrical and heat outputs.

## Implementation

1. Remove the unified-native `StatusPowerBridge` heater instance and its battery/thermal subscriptions.
2. Keep each actual `ThermalHeater.electricalPowerOutMsg` connected to the official Basilisk `SimpleBattery`.
3. Keep the same heater's deposited-heat `powerOutMsg` inside the thermal network's node-local heat aggregation; do not add a second external heat estimate.
4. Derive `eps.loads.heater_power_w` from the sum of the actual per-node `thermal_heater_electrical_<node>` recorder outputs.
5. Add a heater health audit that verifies the summed telemetry matches its per-heater components and that no heater electrical draw exists while the EPS Heater PDU is disabled.
6. Keep per-heater telemetry fields, Run Bundle recorder sources, `thermal_heater_electrical_* ` messages, and the `eps.loads.heater_power_w` trace contract stable.
7. Reduce default unified-native engineering proxy count from one to zero; propulsion-enabled runs retain only the optional propulsion activity proxy.
8. Add a focused official-Basilisk ThermalHeater test proving electrical consumption differs from deposited heat by the configured efficiency, respects power scaling, and fails closed when PDU permission is removed.

## Acceptance gates

- Official upstream Basilisk 2.11.0, not a mock.
- No `StatusPowerBridge` or `project_heater_status_power_bridge` remains in the unified-native whole-spacecraft runtime.
- Every actual ThermalHeater electrical output feeds the battery exactly once.
- Every actual ThermalHeater deposited-heat output feeds its thermal node exactly once.
- `eps.loads.heater_power_w` equals the sum of all per-node actual electrical recorder values.
- PDU-disabled samples have exactly zero total heater electrical load.
- Native heater power scaling and electrical-to-thermal efficiency are independently verified.
- Default unified-native engineering proxy count is zero; propulsion-enabled count is one.
- Existing heater, payload, Comm/Data, ADCS, battery, event, Run Bundle and whole-spacecraft regressions remain green.

## Scope boundary

This slice removes only the unified-native heater proxy. It does not migrate the optional propulsion activity bridge, whose load semantics require a separate physical power-authority decision.


## Merge and acceptance evidence

- PR #198 squash-merged to `main` as `8b269e12dc7ea9652f30bdd6e6a93fd01166996c`.
- Focused P10-A suite on the accepted PR head: 67 passed in 59.83 s; official upstream Basilisk 2.11.0 provenance and offline environment preflight passed.
- Native heater electrical/deposited-heat contract, PDU fail-closed test, unified-native heater/Payload/Comm A/B, static checks, project script/dependency governance, unit-only tests, registered parameter audit, P9-H and finite-run browser tests passed.
- Independent native component/subsystem physical and sealed Run Bundle acceptance passed, including guided browser Run journeys with final proxy inventory 0 nominal / 1 propulsion.
- Evidence artifact: `p10a-native-rw-comm-heater-payload-eps-evidence` (Actions run 44576238049, attempt 2).
- Full native whole-spacecraft regression: PASS, including the 8+ second pilot, 30+ second real-wall-clock endurance/external audit, 180 simulated-second TC/TM soak, and 3600 simulated-second TC/TM soak.
