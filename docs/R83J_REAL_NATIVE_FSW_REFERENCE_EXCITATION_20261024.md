# R83j — REAL Basilisk-native bounded scheduled FSW reference experiment

**Date**: 2026-10-24. **Scope**: upstream SatAgentSimulation research-only evidence, independent of autogen neural diagnosis. **Conclusion**: Native scheduled *attitude guidance* reference was propagated through a genuine Basilisk controller and reactionWheelStateEffector to physically distinct RW angular speed. It is **NOT a scheduled motor torque telecommand**, hardware certification, commanded fault experiment, or proof that a diagnostic model works.

## Frozen engineering setup and evidence

- Upstream experimental source [PR #73](https://github.com/clonetimer/SatAgentSimulation/pull/73) on isolated \`feat/r83j-native-fsw-reference-excitation-lab\`; no Stable/main changes.
- Actual [Actions #40111820380](https://github.com/clonetimer/SatAgentSimulation/actions/runs/40111820380) **ALL PASS**: Linux 3.11 and 3.12, Windows 3.11 protocol/anti-unsafe-command tests plus actual C-backed native dynamics/FSW experiment.
- [Original actual native evidence artifact ID 12565974479](https://github.com/clonetimer/SatAgentSimulation/actions/runs/40111820380/artifacts/12565974479): \`actual_original_native_fsw_schedule.json\` + original wheel compatibility report. Downloaded ZIP SHA-256: \`6f221c7fa3202d7b091f16baef78b001a7db015646b542354922116f790e426e\`.
- Original bsk 2.11.0 wheel SHA-256: \`d51fe1ffa1b0b03dc415034b210497b28a2504428d4d8e71b481542a08c08a0a\`. Wheel ABI metadata was rebuilt as \`2.11.0+satfix1\` with unchanged native payload.
- Native actual source code module: \`src/sat_sim/experiments/native_fsw_scheduled_reference_lab.py\`.
- **Two actual native physical runs**, baseline/reference and scheduled 0.5° x-axis reference, 6 s duration with 0.2 s scheduler period; 31 original recorder samples per arm, no data-fabricated proxy.
- Source guidance changed only at *paused scheduler boundaries*, with downstream original native modules:
  \`inertial3D → attTrackingError → mrpFeedback → rwMotorTorque → reactionWheelStateEffector → spacecraft\` (with \`simpleNav\` source feedback). This is guidance model parameter update, not direct arbitrary RW motor torque write, and must not be advertised as a flight telecommand scheduler.
- Predeclared scheduler pause/reference schedule: \`(0.0 s, 0.0°)\`, \`(1.8 s, +0.5°)\`, \`(3.8 s, 0.0°)\`. Due to discrete 0.2 s task timing, the original native \`attRefOutMsg\` actually records +0.5° MRP reference starting at **2.0 s**. Recorder tracking prevents falsely asserting an instantaneous 1.8 s update.
- All original reference/allocated motor/spacecraft state records are preserved without posthoc clipping.

## Actual physical measurements, original native 2-arm contrast

| Physics QA metric | Actual value | Interpretation |
| --- | ---: | --- |
| Pre-event max paired RW speed difference | **0 rad/s** | Original native physical baseline matched before command |
| Max during-reference native FSW MRP reference difference | **0.0021816650** | A real 0.5° single-axis MRP update from original \`inertial3D\` message |
| Max during-reference RW allocator command difference | **0.0039194724 N·m** | FSW guidance reached native RW allocation |
| Max during-reference RW angular speed difference | **0.0666601340 rad/s** | Real native reaction-wheel dynamics altered (not a Python kinematic mock) |
| Maximum allocated motor-torque *request*, all wheels and both arms | **0.0043633301 N·m** | Below provisional request boundary **0.02 N·m** |
| Maximum observed absolute wheel speed, all wheels and both arms | **31.4159290 rad/s** | Below provisional research speed envelope **900 rad/s** |
| Maximum hub body rate, all axes and both arms | **0.0005193667 rad/s** | Research physics observation, not independent pointing/rate certification |
| Both provisional lab envelope checks | **PASS** | Only the two explicitly coded observer limits; not certified safe operation |

The zero command/reference arm remains stable while the scheduled reference causes a genuine native physical response. The source also records that at 6 s the native guidance reference has returned to zero but the spacecraft/wheels retain the dynamic effects of the pulse, as expected.

## Exclusions and next prospective step

1. **No fault injected in R83j**, so no statement about RW torque-authority-loss, friction or jam *diagnostic detectability* follows from these numbers.
2. The lab changes an \`inertial3D\` C-backed parameter while the simulation is paused. This proves FSW guidance-to-RW actuation coupling, but is not yet an independently verified general event scheduler, replayable telecommand protocol, slew rate governor, or certified torque/current/wheel-speed safety manager.
3. The \`rwMotorTorque\` output is an **allocated wheel motor torque command request**; do not relabel it as direct *measured actuator applied torque*. The RW physical angular velocity comes from the actual \`reactionWheelStateEffector\` state.
4. The provisional 0.5° reference amplitude, 0.02 N·m requested wheel torque bound and 900 rad/s observation bound are research guardrails, not calibrated equipment constraints.
5. The next separately preregistered faulted native test should use genuine Basilisk RW torque-authority-loss injection and genuine references on matched seeds. It must verify a safe request-to-actual-applied-torque path and physical observability *before* any new autogen algorithm training. Maintain existing R83f/R83g \`NO_ELIGIBLE_DESIGN\` conclusions and untouched held-out VAL/FINAL source cohorts.
6. R83i's genuine 36-case static-gain experiment had requested torque up to **5.974× original RW rating** and did not increase normal-pair mean command excitation by increasing controller K/P. R83j's small bounded native reference pulse solves a *different FSW command-chain feasibility question*; it does not erase or override that previous negative result.

**Scientific status:** native guidance schedule coupling **PROVEN BY ORIGINAL BSK SIMULATION**, provisional lab request and wheel-speed thresholds **PASSED**, fault separability, independent safety certification, user-facing TC event scheduling and model eligibility **NOT ESTABLISHED**. Keep this work as a Draft research PR.
