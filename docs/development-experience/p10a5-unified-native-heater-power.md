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


## First focused CI repair round

The first official Basilisk 2.11.0 P10-A focused run reached 67 tests and reported 3 failures, 64 passed:

1. The new PDU-disabled component test used a NumPy recorder array directly in an assertion. NumPy correctly rejects ambiguous truth-value conversion; assert its length instead, then check all samples are zero.
2. The drained-run PDU transition regression used a 0.5 s recorder cadence while the native task period was 0.1 s. The short-lived heater-PDU transition was not represented in the coarse telemetry samples. The test now opts into 0.1 s sampling for that transition scenario; it does not weaken the transition assertion.
3. The propulsion inventory test still expected 'project_heater_status_power_bridge'. It now asserts the heater bridge is absent and the only remaining proxy is the optional propulsion activity bridge (count 1).

These are test-contract and observation-cadence corrections; the runtime model is unchanged in this repair round. Re-run the exact PR head before treating them as resolved.


## Second focused CI repair round

The next official Basilisk 2.11.0 focused run exposed seven failures (60 passed):

1. Six reduced ADCS/event fixtures do not instantiate an EPS PDU. The fast heater-PDU evidence recorder was initially accessed under the broad non-ADCS-only branch and raised 'KeyError: pdu'. It is now created only when the PDU exists, and the health audit accepts an absent fast recorder for fixtures that do not exercise EPS.
2. The drained PDU test changes the heater-permission state, but the thermal thermostats are already off in that scenario. A PDU bit transition without positive heater electrical draw is not an observed power-feedback response. The audit now separates raw PDU-state transition from an actual electrical-load response; the test expects no heater power-feedback claim for that drained scenario while retaining the PDU-shed assertions.
3. The propulsion inventory assertion was aligned to the single remaining optional propulsion proxy; the retired heater proxy is no longer expected.

The runtime continues to use actual 'ThermalHeater.electricalPowerOutMsg' for EPS and actual 'ThermalHeater.powerOutMsg' for deposited heat. No synthetic heater power law or relaxed physical criterion was introduced.


## Third focused CI correction

The follow-up focused run passed the native heater contract, inventory checks, and all other focused cases, but one integration test still expected the heater PDU to be shed in the low-SOC payload/Comm scenario. Actual telemetry showed the heater PDU remained permitted; the battery-drain scenario only shed Payload and Comm. Since the heater thermostats were already off, no heater electrical response was exercised.

Removed only that unsupported heater-PDU-shed assertion. The test still verifies Payload/Comm shedding and explicitly keeps 'heater_pdu_power_feedback_observed' false when no actual heater draw occurs. The separate official Basilisk ThermalHeater contract continues to verify PDU-disabled fail-closed behavior with a real thermostat-on load.
