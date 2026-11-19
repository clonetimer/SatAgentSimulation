# P10-A1: Replace the ADCS power proxy with Basilisk ReactionWheelPower

Date: 2026-11-19
Status: implementation candidate; same-head Basilisk acceptance pending

## Objective

Continue the whole-spacecraft physical-coupling mainline after P9-O3-D. The first vertical slice replaces the ADCS reaction-wheel electrical-load proxy with the official Basilisk `ReactionWheelPower` module, while keeping the spacecraft dynamics and actuator command path unchanged.

This is not a claim that the full electrical motor circuit is modeled. It is a native mechanical-work-to-electrical-load model using the actual reaction-wheel effector state.

## Baseline defect / boundary

The whole-spacecraft graph currently reports four engineering-proxy resource bridges (five when propulsion is enabled):

- ADCS reaction-wheel activity -> EPS/thermal load
- payload data activity -> EPS/thermal load
- communication downlink activity -> EPS/thermal load
- PDU heater status -> EPS/thermal load
- optional propulsion activity -> EPS/thermal load

The ADCS bridge reads a post-driver/post-fault command and a wheel-speed message, then computes `base + |T*Omega|/efficiency`. It is not a winding resistance, voltage, copper-loss or inverter model.

## P10-A1 implementation

1. Instantiate one official `Basilisk.simulation.ReactionWheelPower` per physical reaction wheel.
2. Subscribe each module directly to `reactionWheelStateEffector.rwOutMsgs[i]` (`Omega` in rad/s and `u_current` in N·m actual applied motor torque).
3. Subscribe each module to the PDU ADCS DeviceStatus output, so load shedding disables the native power node.
4. Preserve the existing aggregate base-power assumption by distributing `adcs_power_w` equally across the instantiated wheels.
5. Preserve the existing no-regeneration assumption with `mechToElecEfficiency=-1`; use `elecToMechEfficiency=adcs_drive_efficiency`.
6. Connect each official `nodePowerOutMsg` directly to the official Basilisk `SimpleBattery` and to the existing thermal-network ADCS node.
7. Record every per-wheel native power node and the actual RW state messages. Derive `eps.loads.adcs_power_w` by summing the native per-wheel load messages, not by a presentation-only formula.
8. Keep `adcs_max_power_w` as a fail-closed monitoring envelope. It does not clamp the actuator or change RW dynamics.
9. Remove only the ADCS bridge from `engineering_proxy`; payload, comm and heater bridges remain explicitly classified as proxies. Propulsion remains an additional proxy when enabled.

## Acceptance

- Official Basilisk 2.11.0 runtime constructs one ReactionWheelPower node per real RW and records each output.
- Actual `rwOutMsgs[i].u_current` and `rwOutMsgs[i].Omega` reproduce the official power equation in the recorded ADCS load, within numerical tolerance.
- Zero motor-current limit produces zero applied RW torque while the FSW may still request torque; ADCS power then equals the configured base draw.
- A low but non-zero current limit produces lower ADCS power than the nominal case.
- PDU ADCS disable turns the official power nodes off; the native battery net power responds to the load difference.
- The same power messages feed the thermal ADCS node.
- Whole-spacecraft proxy count decreases from 4 to 3; propulsion-enabled count decreases from 5 to 4.
- ADCS-only runs retain the previous reaction-wheel dynamics; the EPS power-accounting change does not add a wheel power limit or alter `P_max`.
- Cross-platform contract tests and the official native Basilisk workflow pass on the same head.

## Explicit limitations

- `ReactionWheelPower` uses mechanical power `Omega * u_current`, base draw and a declared conversion efficiency.
- This does not model motor winding resistance, bus voltage, copper losses, inverter switching losses, electrical current dynamics or a calibrated motor driver.
- The base draw and conversion efficiency remain engineering assumptions until a target wheel datasheet or calibrated reference is approved.
- No flight qualification or hardware calibration is implied.
- Payload, communication, heater and optional propulsion power bridges remain unresolved and must be tackled as separate vertical slices; do not relabel them native prematurely.

## Evidence references

- Basilisk ReactionWheelPower module documentation: https://avslab.github.io/basilisk/Documentation/simulation/power/ReactionWheelPower/ReactionWheelPower.html
- Basilisk reactionWheelStateEffector output messages: https://avslab.github.io/basilisk/Documentation/simulation/dynamics/reactionWheels/reactionWheelStateEffector.html
- Basilisk 2.11.1 `simIncludeRW` documents the optional physical `P_max` wheel power limit. This task intentionally leaves it disabled, because a reporting model must not silently change wheel dynamics.
