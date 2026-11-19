# P10-A3: Native thermal-heater electrical power coupling

Date: 2026-11-19

Status: implementation candidate on `feat/p10a3-native-heater-power-20261119`; same-head Basilisk 2.11.0 acceptance pending.

## Objective

Remove the project-owned `HeaterPowerFeedback` aggregation bridge from the active whole-spacecraft native thermal-network path. Connect each thermal heater's existing `electricalPowerOutMsg` directly to the official Basilisk `SimpleBattery`.

## Baseline and rationale

- `ThermalHeater` already publishes three distinct outputs: binary `heaterOutMsg`, deposited thermal power `powerOutMsg`, and actual electrical load `electricalPowerOutMsg`.
- `electricalPowerOutMsg` is computed from the heater's actual on/off state, configured rated power, and runtime `power_scale`; it is not a reconstructed nominal-wattage estimate.
- The prior `HeaterPowerFeedback` reread the heater's binary state and reconstructed the load from nominal `params.power_w`, which could diverge from the component's scaled electrical output.
- The active native thermal-network path should consume the component-owned electrical output directly. The reduced legacy thermal branch remains on the existing bridge until separately migrated; do not relabel that fallback as native.

## Implementation

1. Connect each native thermal heater's `electricalPowerOutMsg` directly to `battery.addPowerNodeToModel`.
2. Preserve the heater's PDU enable input and thermal state model unchanged.
3. Record each heater electrical output independently and aggregate measured recorder values into `heater_eps_load_w` and electrical-energy accounting.
4. Mark `thermal_heaters_to_eps_load` as `coupled` only for the native network path; preserve the explicit legacy fallback status for the reduced thermal branch.
5. Add same-head tests for on/off behavior, rated-load scaling, and paired battery response.
6. Include the regression in P10-A, full native Basilisk, and static compile CI workflows.

## Acceptance gates

- Official upstream Basilisk 2.11.0 execution, not a mock.
- Every native thermal-network heater output is attached exactly once to the official `SimpleBattery`.
- Heater ON produces `-rated_power * power_scale`; heater OFF produces 0 W.
- Paired ON/OFF runs show the expected load difference in the native battery after the task feedback boundary.
- Summary telemetry and Run Bundle evidence expose the sum of actual per-node recorder outputs.
- Native thermal-network path no longer instantiates `HeaterPowerFeedback`; reduced legacy thermal path remains unchanged and explicitly proxy-classified.
- Existing thermal temperature response, PDU heater permission, thermal faults, battery, Comm, ADCS, propulsion and whole-spacecraft regressions remain green.

## Model boundary

The configured heater wattage and power scaling remain engineering-model parameters. This migration uses the thermal component's actual electrical output but does not qualify the heater hardware, resistance, PWM electronics, or flight thermal design.
