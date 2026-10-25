# R83l original 54-case Basilisk-native RW multi-condition experiment

**Execution date: 2026-10-25. Status:** 54 actual genuine-Basilisk native physical cases executed, full 3-platform software protocol passed. Study is **engineering physics only**, not automatic fault detection, independent mission qualification, or model-comparison evidence.

## Original physical source

- [Original GitHub Actions run #40156825783](https://github.com/clonetimer/SatAgentSimulation/actions/runs/40156825783), PASS; full original bsk==2.11.0 wheel SHA256 \`d51fe1ffa1b0b03dc415034b210497b28a2504428d4d8e71b481542a08c08a0a\`, native binary payload unchanged after Python-version metadata compatibility repackage.
- First executed experimental source commit \`230a2f3c06477d02b6dd0ffcaed7e7e762a69b27\`, with independent [original GitHub artifact](https://github.com/clonetimer/SatAgentSimulation/actions/runs/40156825783/artifacts/12585595456).
- Original downloaded ZIP SHA256 \`71e841db657e3f17a65a28cc56fc2e2bd6c6de7f06267760614f8cae70ebcf4c\`; original **2,021,087-byte** real-native physics JSON SHA256 \`45ff210c05828873a4820fd7f2ea51f85e49578b47e88ed6deb8957222d5bc96\`. The 54 original physical messages are preserved **unmodified**.
- [autogen read-only source proof #40157485052](https://github.com/clonetimer/autogen/actions/runs/40157485052), PASS, re-computed each C-backed native RW derating, 36 paired physics metrics and all 3-wheel requested torques. Original downstream audit artifact [#12585047504](https://github.com/clonetimer/autogen/actions/runs/40157485052/artifacts/12585047504). Audit ZIP SHA256 \`3c53723a7129c614199e443076d1587c74916562abd91dc39d941d102ef6d4df\`; original audit JSON SHA256 \`ff958047eed0e1412ebe590b47e2352e5e3c4e6f3f5d234d5a6f5701d4cbd4a4\`.

## Frozen source experiment: do not double-count dependent runs

| Controlled original variable | Values |
|---|---|
| RW target (three orthogonal actuator axes) | RW0 / RW1 / RW2 |
| Initial MRP state | 0, small offset A, small offset B |
| Native inertial3D guidance | zero or bounded 0.5° scheduled step on selected RW axis |
| Target RW physical motor torque cap | original 0.020 N·m, reduced 0.005 N·m (75% loss), reduced 0.001 N·m (95% loss) |
| Native source cases | **54** (3×3×2×3), 31 native samples each |
| Healthy controls reused | **18**; do not duplicate across the 75/95% contrasts |
| Fault/normal paired physics contrasts | **36** |
| Distinct wheel/initial-state configurations | **9**; NOT nine independently manufactured spacecraft |

All cases use the same real C-backed Basilisk spacecraft, inertial3D → attTrackingError → mrpFeedback → rwMotorTorque → reactionWheelStateEffector, with natural RW dynamics. At t=2.4 s the actual wheel target's original C-backed \`u_max\` is set while the engine is paused, then verified by the *actual* \`ReactionWheelData[wheel].u_max\` readback. Native \`rwOutMsgs[wheel].u_current\` is actual simulated applied motor torque; native allocator command is a *request*, not interchangeable with actual torque.

## All matched physical source results

| Original 9 wheel/initial configurations | Fault effect on RW speed (strictly positive, >1e-8) | Median maximum postfault target-wheel speed difference |
|---|---:|---:|
| Zero reference, 75% loss | **0/9** | 0 rad/s |
| Zero reference, 95% loss | **0/9** | 0 rad/s |
| Scheduled 0.5° reference, 75% loss | **0/9** | 0 rad/s |
| Scheduled 0.5° reference, 95% loss | **9/9** | **0.0275243625 rad/s** |

For scheduled reference under 95% torque-authority loss, wheel-specific median speed differences across three initial conditions were RW0 **0.0295111**, RW1 **0.0275244** and RW2 **0.0231807 rad/s**. Across the whole original source, maximum native allocated motor request **0.004881606711 N·m** (below provisional 0.020 N·m), maximum wheel speed **31.479712328 rad/s** (below provisional 900 rad/s), maximum body rate **0.000828166403 rad/s**. These are provisional research observations **NOT mission/flight safety certification**.

**Mechanistic source explanation for 75% non-manifestation:** its physical residual motor capacity \`u_max=0.005 N·m\` is larger than the original maximum requested command **0.004882 N·m**. Therefore actual native actuator cap never binds in any 75%-loss case; physical wheel-speed and motor-torque differences remain exactly zero. A classifier cannot reliably infer an inactive actuator capacity constraint from those equal native observations. This is not evidence that a bigger neural network should succeed.

## Material defect disclosed in ORIGINAL source metadata

A real downstream audit caught a Python local-variable shadowing defect **after** successful native physics: inside the original exporter, \`wheel\` (integer intended actuator index) was overwritten by the last sample's *three-element wheel-motor request vector*. This corrupted **54/54** original source metadata values: \`source_fault_target_index_QA_ONLY\`, \`native_independent_condition_id\`, \`native_reference_context_id\`. **It did not change** actual C-backed wheel selection, original case dictionary keys, injector event \`physical_fault_target_rw_index_QA_ONLY\`, native \`ReactionWheelData[target].u_max\` readback, original FSW states or recorded source messages.

Original research archive remains **immutable and SHA-256 pinned**, including this known defective metadata. The independent autogen evidence verifier requires the exact, auditable bug signature: affected index is a numeric vector identical to the **last native motor-command sample**, and the two affected textual group IDs are formatted from that same exact vector. It then reconstructs a verified original target ONLY by matching case name, original scheduler event target, actual C-backed actuator capacity readback and wheel-indexed native source messages. Its receipt explicitly reports \`original_case_exporter_metadata_shadowed_count=54\` and \`original_source_metadata_wheel_target_trusted=false\`. Unknown metadata drift is REJECTED.

The future code generator was corrected to use \`wheel_commands\` for recorder values so that wheel target remains an integer; a separate Linux/Windows contract regression protects this fix. **Original 54-case physical simulation was NOT re-run to manufacture cleaner results.** This metadata repair does not retrospectively cure scientific limitations.

## Scientific and governance status

**Physical C-backed RW fault presence demonstrated** for selected severe loss under bounded guidance; **moderate 75% loss remains structurally unobservable** under this excitation; **diagnostic classifier never trained or qualified**. No VALIDATION, TEST, FINAL cohort accessed. No on-board sensor measurement of native applied torque is assumed. The actual wheel/initial conditions are not independent physical spacecraft/equipment, and no real equipment uncertainty, sensor noise, current/thermal/pointing safety envelope, or deployment-generalization test was conducted.

Next prospectively register a different safe native **guidance excitation amplitude/trajectory** that genuinely requests above the 0.005 N·m loss75 residual capacity while remaining under independently grounded hardware and mission limits, with prospective physical conditions and a separate protected evaluation cohort. Do not retrospectively amplify the successful 0.5° result or lower prior R83f/R83g classifier acceptance thresholds. Current PRs remain Draft; Stable remains untouched.
