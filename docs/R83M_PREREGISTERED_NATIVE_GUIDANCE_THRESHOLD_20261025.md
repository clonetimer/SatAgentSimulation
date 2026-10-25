# R83m PREDECLARED genuine Basilisk RW excitation-threshold experiment

**Research only.** This protocol is frozen **before the first R83m physical
simulation result is inspected**, motivated by existing published **R83l**
results (0.5° guidance peak RW command 0.0048816067 N·m < 0.005 N·m
capacity remaining after 75% torque loss). Thus R83m is a *prospective
followup conditional on R83l*, NOT an independent prospective replication
of that prior hypothesis, not confirmatory generalization, and not a flight
hardware operation.

## Scientific question

Will an **a priori** increased native FSW attitude-reference pulse from
0.5° to 0.9° cause actual native RW motor torque saturation after **75%
RW0/RW1/RW2 torque authority loss**, making physically paired native
wheel-speed divergence nonzero? An answer of *no* remains a valid negative
result; do not select a different pulse, dynamic seed or severity following
this evaluation.

## Frozen source design

- Original native implementation: Basilisk bsk==2.11.0 (original wheel
  binary SHA256 \`d51fe1ffa1b0b03dc415034b210497b28a2504428d4d8e71b481542a08c08a0a\`),
  unchanged C-backed module chain
  \`inertial3D→attTrackingError→mrpFeedback→rwMotorTorque→
  reactionWheelStateEffector→spacecraft\` and original R83l plant.
- One real physical affected RW: **0, 1 or 2**; same 3 preregistered
  deterministic initial attitude vectors from R83l, so **9 design
  condition groups**, never 54 independent spacecraft.
- Guidance conditions **0°, 0.5°, 0.9°**, one axis aligned with the
  target wheel; command at paused native scheduler 1.8 s, command leaves
  at 3.8 s, 0.2 s fixed process/task time grid. Recorded native
  messages may first reflect the change at 2.0 s. Max reference 0.9°,
  not a raw direct motor torque message.
- Only capacity states **healthy 0.020 N·m** and **75% derating
  0.005 N·m**, with live native C++ \`ReactionWheelData[wheel].u_max\`
  readback at the paused 2.4-s physical fault boundary.
- Total: **3×3×3×2 = 54 native physical cases**, 27 healthy arms,
  27 paired derated arms. There is **no new 95% loss arm**, no posthoc
  amplitude choice and no generated VAL/TEST/FINAL cohort. Each fault
  twin has exactly the same original initial state, native dynamics and
  guidance schedule as its normal twin until fault onset.
- Preregistered provisional original all-wheel command request bound
  **0.020 N·m**, all-wheel speed bound **900 rad/s**, hub angular rate
  bound **0.005 rad/s** over every source sample. Inference of
  mission-level safe attitude pointing/current/power **is prohibited**:
  these three are research observation limits, not certified hardware.
- Record all 54 original native 31-sample outputs, all-wheel allocator
  requests, native actual target RW \`rwOutMsgs[target].u_current\`,
  real RW speed and spacecraft state, original physical source receipts,
  true C++ RW derating and requested guidance reference.

## Predeclared analysis

All 27 matched healthy-vs-75%-derated native pairs must be summarized.
For each pulse amplitude: (a) number of pairs with native target wheel
speed post-fault maximum paired divergence > 1e-8 rad/s, (b) native
target wheel *applied motor torque* post-fault maximum difference >
1e-8 N·m, (c) maximum and median paired differences; (d) maximum
**requested** RW torque relative to **original** 0.020 N·m hardware
capability and physical derated 0.005 N·m capacity. Negative results
must not be excluded from counts. Check all 3 wheels for physical request
and speed bounds, and full body axis rates against the provisional
0.005 rad/s bound. Do not accept an invalid mixed source/target index,
premature physics fault influence or a model inference feature using
injected wheel/onset/source-internal \`u_current\`.

**Invalid/censored research-run outcomes:** if native engine cannot
execute, missing source evidence, physical actuator not derated, source
contradicts paired initial state, or provisional observer bound is
exceeded, mark as FAILED and retain evidence. Do NOT weaken bounds or
silently rerun until success.

## Explicit exclusions

No neural network training, no VAL/TEST/FINAL, no diagnostic model
performance, no claim that an original physical sample is independent
real equipment, no physical absolute mission pointing/current/power
certification. \`autogen\` R83f/R83g **NO_ELIGIBLE_DESIGN** stays
intact, even if R83m shows a visible motor response. Experimental
source PR stays Draft, no Stable/main merge.
