# P10-A6 native propulsion electrical accounting

Date: 2026-11-20

## Intent and scope

Continue the P10-A resource-coupling mainline from main `61a0ab2d`. Both active
whole-spacecraft runtimes still use `ThrusterActivityPowerBridge`. Replace that
electrical power equation with official Basilisk 2.11.0 `SimplePowerSink` nodes,
one per actual thruster. Retain propulsion dynamics, fuel use, mission command
issuance, existing telemetry totals and heat destinations.

The user requested direct continuation. The implementation choices below are
engineering decisions in this slice, not new user requirements.

## Findings and chosen model

The old bridge multiplies a configured 20 W by each `thrustFactor`. No component
calibration supports a linear thrust-factor-to-electric-power curve. The new
model is an explicitly configured fixed auxiliary electrical load per active
thruster (valve/controller activity), with zero idle draw. It is neither exhaust
power nor a chemical/mechanical-to-electric conversion model. Keep the existing
0.85 electrical-to-local-heat fraction as an engineering assumption.

`PropulsionBurnController.permissionStatusOutMsg` permits initial command
issuance only. It does not abort an already issued burn. Using it as a power
supply switch can erase consumption during a continuing burn. The new status
adapter therefore follows actual `THROutputMsg.thrustFactor > 0`, and does not
subscribe to burn-start permission. Low SOC before ignition still prevents the
burn and naturally results in zero propulsion draw.

## Approaches considered

1. Keep the factor-proportional bridge: retains an unsupported electrical law
   and the permission/accounting mismatch.
2. Add a detailed valve/driver circuit model: needs hardware data not available
   in this project and exceeds this continuation.
3. **Chosen:** a status-only adapter and an official fixed-load sink per
   thruster. It makes the power authority explicit, removes the custom power
   equation, and preserves the limitations of the activity-based assumption.

## Interfaces and constraints

- Shared `ThrusterActivityStatusAdapter(model_tag, *, thruster_output_msg)`
  publishes `activeStatusOutMsg` and a diagnostic trace. It owns no watts or
  electrical-power output.
- Reject missing inputs; fail closed and report invalid/unwritten/non-finite or
  out-of-range factors. A runtime input error must prevent a PASS health claim.
- Both runtimes connect the same official power output to the battery and the
  existing thermal consumer exactly once. Sum real per-node recorder outputs.
- Retain aggregate propulsion telemetry and derive cumulative energy from the
  official recorder samples. Keep per-thruster evidence available.
- Do not change physical command, thrust, mass flow or orbit equations.
- Zero custom propulsion power proxies does not mean hardware calibration or
  an entirely official spacecraft model; the adapters and thermal model remain
  project-native.

## Acceptance

Real official Basilisk 2.11.0 tests must cover fixed active load, zero activity,
invalid input, independent thrusters, battery and heat A/B, unchanged native
thrust/fuel/orbit under a power-only A/B, no-start low SOC, and continued draw
when start permission drops during an ongoing burn. Re-run the existing focused
P10-A and physical/Run Bundle/browser gates on the final PR head.
