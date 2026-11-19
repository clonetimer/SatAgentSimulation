# P10-A3: Native thermal-heater power coupling

Date: 2026-11-19

Status: implementation candidate on `feat/p10a3-native-heater-power-20261119`; same-head Basilisk 2.11.0 acceptance pending.

## Objective

Remove the project-owned `HeaterPowerFeedback` aggregation bridge from the active whole-spacecraft native thermal-network path. Represent each binary thermal heater as an official Basilisk `SimplePowerSink` whose `nodeStatusInMsg` subscribes directly to that heater's `heaterOutMsg`.

This is a fixed-load electrical model for a binary heater. It does not claim heater resistance, PWM, voltage/current dynamics, thermostat circuit behavior, or calibrated hardware parameters.

## Baseline and rationale

- The native thermal network already emits one `DeviceStatusMsg` per heater and exposes the configured heater power in watts.
- Basilisk `SimplePowerSink` supports an optional `nodeStatusInMsg`; status 0 disables the fixed power node and status 1 enables it.
- The previous `HeaterPowerFeedback` was a project-owned status-to-`PowerNodeUsageMsg` equation. No continuous or analog power computation is required for this binary heater profile.
- The native thermal-network branch is the target. The reduced legacy thermal branch remains explicitly on the existing bridge until separately migrated; do not relabel that fallback as native.

## Implementation

1. Create one official `SimplePowerSink` per thermal-network heater with `nodePowerOut=-heater.params.power_w`.
2. Subscribe each sink's official `nodeStatusInMsg` directly to the matching `heater.heaterOutMsg`.
3. Add every official sink output to the native `SimpleBattery`.
4. Record each heater power-node output separately and aggregate the measured recorder values into `heater_eps_load_w` and electrical-energy accounting.
5. Mark `thermal_heaters_to_eps_load` as `coupled` only for the native network path; preserve the explicit legacy fallback status for the reduced thermal branch.
6. Add same-head tests for on/off behavior, per-heater load magnitude, PDU gating through the actual heater state, and paired battery response.
7. Include the regression in P10-A, full native Basilisk, and static compile CI workflows.

## Acceptance gates

- Official upstream Basilisk 2.11.0, not a mock.
- Each native thermal-network heater has exactly one official `SimplePowerSink`.
- Each sink's `nodeStatusInMsg` is linked to the matching heater output.
- Heater ON produces exactly the configured negative load; heater OFF produces 0 W.
- Paired ON/OFF runs show the expected load difference in the native battery after the task feedback boundary.
- Summary telemetry and Run Bundle evidence expose the sum of actual per-node recorder outputs, not a presentation-only estimate.
- Native thermal-network path no longer instantiates `HeaterPowerFeedback`; reduced legacy thermal path remains unchanged and explicitly proxy-classified.
- Existing thermal temperature response, PDU heater permission, thermal faults, battery, Comm, ADCS, propulsion and whole-spacecraft regressions remain green.

## Model boundary

The configured heater wattage is an engineering assumption. This migration changes the Basilisk power-node implementation, not the source or calibration quality of the heater rating. It does not qualify the heater hardware or flight thermal design.
