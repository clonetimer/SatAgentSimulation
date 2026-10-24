# R83k — ORIGINAL 4-arm Basilisk-native FSW × RW torque authority physical fault (2026-10-24)

**Status: ACTUAL PINNED NATIVE EXPERIMENT DONE.** [SatAgentSimulation Actions #40114200932](https://github.com/clonetimer/SatAgentSimulation/actions/runs/40114200932): Linux Python 3.11/3.12 and Windows 3.11 preregistration contract tests PASS; original **bsk 2.11.0** with unchanged native payload executed exactly **four true spacecraft/FSW/RW dynamics runs** and saved original 31-point time histories per arm. [Draft PR #77](https://github.com/clonetimer/SatAgentSimulation/pull/77), stacked on R83j PR #73. Main and Stable not changed.

## Evidence provenance and scientific limits

- Native original 2×2 experiment head: \`74e680e0f86f84707fd69116e7e14c1601ba9343\` (actual Actions run). Original Basilisk wheel: SHA256 \`d51fe1ffa1b0b03dc415034b210497b28a2504428d4d8e71b481542a08c08a0a\`; native payload bytes unchanged by wheel metadata compatibility packaging.
- GitHub native physical-source artifact: [#12567229709](https://github.com/clonetimer/SatAgentSimulation/actions/runs/40114200932/artifacts/12567229709), \`r83k-real-basilisk-native-fsw-faulted-rw-capacity-2x2\`. Original downloaded ZIP SHA256 \`21076917e32671d12cff060d6163e788945e821255571282705e2c0adc47400c\`.
- Exact archived original \`actual_r83k_original_native_faulted_reference.json\` SHA256 \`4b6fd9b66e48b9be7ff9247eb8d83a36cd0d3977e7616df091a087671e42ce56\` (134075 bytes), containing all native references, wheel allocation commands, native RW output **u_current**, wheel speeds, spacecraft states and fault-capability C-backed readback receipts. \`compat_payload_review.json\` SHA256 \`3f279f92c4e424ce0625083a190dda1b9ab183727fb789eb976abded67b9cdb9\`.
- No time-series synthetics, Python rigid-body dynamics proxies, post-hoc fabricated applied torque, model training, VAL/TEST/FINAL or independent hardware measurements. This is a **small, deterministic engineering source feasibility study**, NOT a diagnostic classification benchmark, not a repeatability or generalization study, and not flight certified.

## Frozen factorial interventions

- Baseline original RW0 C-backed \`RWConfigPayload.u_max=0.0200 N·m\` across all three native reaction wheels, standard original wheel initial states, identical 6-second plant and 0.2-second Basilisk task grid.
- Factor A: native \`inertial3D\` zero reference vs paused-scheduler 0.5° x-axis setpoint (set at 1.8s; native message samples first show nonzero at 2.0s; reset requested at 3.8s).
- Factor B: **normal RW0** vs **genuine actuator torque capacity loss**. At native scheduler boundary 2.4s, change live RW0 original C-backed physical \`u_max\` from 0.0200 to **0.0005 N·m** (97.5% derating); verify original shared \`reactionWheelStateEffector.ReactionWheelData[0].u_max\` readback equals 0.0005. Earliest post-event physics timestep is 2.6s. Three RW wheels remain mechanically attached to actual spacecraft dynamics.
- Exactly 4 physical arms \`zero_healthy\`, \`step_healthy\`, \`zero_derated\`, \`step_derated\`. Normal/fault twins have identical FSW attitude reference and initial state; the true physical fault parameter is never used as a deployment inference feature.
- Actual output \`rwOutMsgs[0].u_current\` is the *native RW simulation's applied motor torque*, **not** the upstream allocator request; faulted post-onset |u_current| is bounded by the derated 0.0005 N·m.

## Actual numerical physical findings

All metrics are computed directly on original native physical recordings, not classifier predictions.

| Within-reference physical pair | Zero reference | Scheduled 0.5° reference |
|---|---:|---:|
| Max RW0 wheel-speed difference before fault | 0 rad/s | 0 rad/s |
| Max RW0 speed difference **after** fault | **0 rad/s** | **0.039641376785 rad/s** |
| Max RW0 actual **applied** torque difference after fault | **0 N·m** | **0.003014143317 N·m** |
| Peak faulted actual applied RW0 torque after event | 0 N·m | **0.0005 N·m** |
| Native fault shared-pointer C-backed cap readback | 0.0005 N·m | 0.0005 N·m |

Across all four native cases, all-wheel maximum requested motor torque **0.004363330053 N·m**, below preregistered **0.02 N·m** research request observation threshold. Maximum observed wheel speed **31.415928985 rad/s**, below provisional **900 rad/s** research observation threshold. **Both programmed provisional bounds PASS.** Neither was independently calibrated to user's mission or flight equipment.

### Scientific interpretation

This is direct **within-simulator physics proof of input-dependent fault observability**: a strong RW torque-authority-loss with zero reference causes no measurable RW response, because the controller is not commanding wheel torque. A small scheduled native FSW reference generates a genuine commanded motion, permitting the same true actuator limitation to propagate into the actual C-backed applied torque and real reaction-wheel speed.

This finding is **not** proof of healthy/fault automatic classification: four deterministic cases from one plant, one affected wheel, one severe 97.5% capacity reduction, one reference pulse, one runtime environment, no realistic sensor noise/calibration/alternative faults, no independent groups. A healthy/fault speed residual exists only because paired simulations can be compared in offline research; *a deployed detector would not have a matched healthy-twin trace or the source-internal applied torque / onset / target wheel as labels at runtime*.

## Research policy and prospective next milestone

1. Preserve the original 4-arm physical receipts and reproducibility SHA without selectively rerunning a better signal or retuning the 0.5°/0.0005 N·m experiment.
2. In a separate preregistered protocol, expand **physical run groups**, inclinations, injected wheel identities, moderate and severe torque reductions, current/voltage and noise constraints, and mission-credible pointing slew/speed bounds. Keep hardware/FSW authority limits and stop conditions fixed **before** simulations.
3. Only after original native source passes independently reviewed physical observability and source-role leakage gates, compare registered fixed/ML/static/adaptive model families with equal split/fold/fit budget *and resource accounting*, no retrospective VAL/FINAL tuning.
4. R83f/R83g original \`NO_ELIGIBLE_DESIGN\` findings remain unchanged. Source applied torque, fault wheel index, onset and simulator truth are offline physical QA only and MUST NOT be made algorithm observation fields.
5. This test-only mutator writes a paused \`RWConfigPayload.u_max\`, not a production flight command, dynamic user-configurable FSW schedule or certified spacecraft control law. Real physical actuation safety and diagnostic generalization remain **NOT ESTABLISHED**.

**Bottom line: original Basilisk FSW scheduled guidance × real RW physical capacity loss → measurable response demonstrated; model eligibility and flight qualification still refused.**
