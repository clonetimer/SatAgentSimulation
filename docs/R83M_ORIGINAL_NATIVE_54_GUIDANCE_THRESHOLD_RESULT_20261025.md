# R83m — Actual REAL Basilisk guidance-threshold / 75% RW torque-loss pilot

**Completed 2026-10-25 (UTC).** This is **original Basilisk physical-source evidence**;
it is not classification accuracy, fault detection in sensor noise, independent
spacecraft generalization, a flight command scheduler, or a certified safe maneuver.

## Original source and immutable receipts

- [R83m research PR #88](https://github.com/clonetimer/SatAgentSimulation/pull/88),
  stacked on R83l PR #84; Stable/main unchanged and PR remains Draft.
- **Before the original R83m physical run**, exact experiment design registered in
  \`docs/R83M_PREREGISTERED_NATIVE_GUIDANCE_THRESHOLD_20261025.md\`:
  3 wheels × 3 original initial spacecraft attitudes × 3 fixed reference
  amplitudes (0°, 0.5°, 0.9°) × healthy/75%-loss actuator = **54 original
  native physical cases**. **9 wheel/initial condition groups**, 27 healthy
  and 27 faulted source cases, **27 matched normal/fault comparisons**.
- [Actual genuine Basilisk run #40160788578](https://github.com/clonetimer/SatAgentSimulation/actions/runs/40160788578)
  **PASS** on Ubuntu Python 3.11/3.12, Windows Python 3.11 original-source
  contract testing, and 54 C-backed original Basilisk physics cases.
  First real successful source commit \`6e1d54bfef6655a29e0a6c98b135f478fa539a48\`.
- [Original source artifact #12586915899](https://github.com/clonetimer/SatAgentSimulation/actions/runs/40160788578/artifacts/12586915899),
  \`r83m-genuine-basilisk-54-native-guidance-threshold-75pct-loss\`,
  ZIP SHA-256 \`db83c8b50a833d2f9c5074cb3f6f9d44c8e0109a2a7be4ddcec5f70bff754796\`.
  Inside: exact original \`r83m_genuine_native_54_original.json\`
  **SHA-256 \`635fd4e4cd36da39a15ba1289003ba84aaf4551b02de70c3d063ada878209502\`**,
  2,020,615 bytes of original native 54×31-point physics records,
  plus original wheel binary/payload compatibility report.
- Same original Basilisk \`bsk==2.11.0\` binary SHA256
  \`d51fe1ffa1b0b03dc415034b210497b28a2504428d4d8e71b481542a08c08a0a\`;
  original native payload unchanged. No Python rigid-body approximation.
- Cross-check [autogen independent original-source Actions #40161374796](https://github.com/clonetimer/autogen/actions/runs/40161374796)
  **PASS** across all 3 platforms and original SHA-pinned source:
  54 original case signatures, correct target wheel (no R83l metadata
  shadow), true C++ RW capacity readback, actual \`rwOutMsgs[target].u_current\`,
  true inertial3D MRP time/axis, all 27 matched source pairs, request and
  rate/speed bounds independently recomputed.
- [Autogen audit artifact #12586083694](https://github.com/clonetimer/autogen/actions/runs/40161374796/artifacts/12586083694):
  ZIP SHA256 \`6ef6d5f58c66e8d2f5839fafa27cbdbb3a5c6ad32ab8330e3916e6aa79c28ddc\`,
  audit JSON SHA256 \`ec2ce9521ad87a745656d0661db502995ee6607a43f4b75737405300e70ff889\`.

## Original intervention and physical mechanism

Basilisk \`inertial3D→attTrackingError→mrpFeedback→rwMotorTorque→
reactionWheelStateEffector→spacecraft\` connects C-backed FSW guidance
reference to real spacecraft and three original RW effectors.

- Source physical motor rating: **0.020 N·m** per wheel.
- At 2.4 s paused native scheduler boundary, source-internal real C-backed
  \`ReactionWheelData[target].u_max\` is reduced to **0.005 N·m**
  for fault arm only, i.e. **75% physical torque authority loss**; confirmed
  by C++ readback (not by a fabricated fault label). Native applied motor
  torque \`rwOutMsgs[target].u_current\` is recorded separately from motor
  allocator *requested* wheel torque.
- Original FSW attitude reference is requested at 1.8s; pulse appears in
  actual 0.2s native recorder at **2.0s** and disappears at 4.0s.
  Three amplitudes **0°, 0.5°, 0.9°**, each on the affected wheel
  control axis. Original healthy/fault pair shares all physical initial
  conditions and guidance, allowing isolated native fault effect.
- Every source case has 31 native physical samples, all three wheel
  commanded torques, wheel speeds, spacecraft attitudes/angular rates.
  No fault labels or simulator-private applied torque are passed to
  deployable diagnosis/inference.

## Actual 27 matched original physical comparisons

| Native guidance reference | Paired physical source groups | Speed effects >1e-8 rad/s | Median maximum post-onset RW-speed effect (rad/s) | Max physical speed effect (rad/s) | Physical motor-torque effect groups |
|---|---:|---:|---:|---:|---:|
| 0° | 9 | **0/9** | 0 | 0 | 0/9 |
| 0.5° | 9 | **0/9** | 0 | 0 | 0/9 |
| 0.9° | 9 | **9/9** | **0.00520032949** | **0.01059166308** | **9/9** |

At 0.9°, each actual physical faulted RW reaches the post-fault applied
torque cap **0.005 N·m**, while nominal original actuators can deliver the
larger requested amount. The faulted case then exhibits different actual
native RW speed despite identical guidance and baseline dynamics.
At 0.5°, all 9 paired physical wheels remain below the derated torque
cap; there is no applied torque/wheel-speed difference. At zero
reference, the same source physical capability loss need not manifest
under these closed-loop initial conditions.

Original globally measured wheel allocator requests for each reference arm:

| Native reference | Maximum absolute requested motor torque (N·m) | Max body angular rate (rad/s) |
|---|---:|---:|
| 0° | 0.00200000 | 0.000368465 |
| 0.5° | 0.004881607 | 0.000828166 |
| 0.9° | **0.008372305** | **0.001243703** |

Original max absolute wheel speed across all cases **31.49274423 rad/s**.
Original provisional bounds fixed BEFORE simulation: requested motor torque
≤0.020 N·m, wheel speed ≤900 rad/s, all-axis body rate ≤0.005 rad/s.
**All three numerical research observer bounds PASS; safety certification
does NOT follow.** Source-native body rate may remain operationally too
large for a given high-resolution payload pointing requirement; no
hardware current/voltage or pointing precision limit was independently
qualified.

## Fair research interpretation

1. **R83l earlier negative result remains true:** a real 75%-loss actuator
   was not dynamically distinguishable under its preregistered 0.5° pulse.
   R83m's stronger 0.9° intervention extends the physics regime; it does
   **not** retroactively improve R83l model or modify its source.
2. 0.9° was chosen prospectively for THIS run but with knowledge of
   R83l's small physical command-request margin. Hence R83m is exploratory,
   not an independently blinded confirmation.
3. This is **one simulator model family**, three selected wheel directions,
   three deterministic initial attitudes and one fault severity; 9 design
   conditions are **NOT nine statistically independent flight systems**.
4. Offline matched healthy-vs-fault residual uses a physical matched twin
   unavailable to a deployed detector. Motor applied torque, original
   target wheel and fault onset are **physics QA PRIVATE** (not sensors
   assumed available to deployed models). A physical simulator difference
   at 0.0052 rad/s may be smaller than realistic gyro/RW-speed measurement
   noise; no sensor-noise detectability, diagnostic classification error,
   cross-environment robustness or false-alarm rate is claimed.
5. R83f/R83g strict algorithm \`NO_ELIGIBLE_DESIGN\` remains unchanged;
   untouched VALIDATION/TEST/FINAL and independent equipment cohorts.
   R83m trained **ZERO** diagnostic models.

**Conclusion:** the native physics does produce excitation-dependent
75%-torque-authority-loss wheel response for THIS fixed 0.9° lab
reference without exceeding three provisional observer limits. Flight
safety certification and actual intelligent fault-diagnosis eligibility
remain **NOT ESTABLISHED**. Preserve both positive and negative evidence,
keep Draft and Stable untouched.
