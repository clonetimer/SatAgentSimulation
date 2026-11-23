# DT-2 / seeded IMU bias random walk — implementation and evidence contract

**Status:** stacked candidate on IMU quantization PR #219; not yet merged. Exact test evidence must be read from the resulting PR HEAD. This document distinguishes the model boundary from official Basilisk sensor internals.

## Model boundary and rationale

The whole-spacecraft graph already uses the official Basilisk `ImuSensor` for truth, fixed bias, white noise, LSB quantization, saturation and platform mounting. The new `ImuBiasRandomWalk` is a **project-owned Basilisk-scheduled SysModel**, not a modification of Basilisk C++ or a claimed OEM implementation.

The SysModel updates the public `ImuSensor.senRotBias` and `senTransBias` properties **before** the official sensor's scheduled `UpdateState`. Thus the bias walk is part of the official IMU measurement path and is subsequently included in native delta-angle / delta-velocity integration, native quantization and native saturation. It is not added to telemetry after the sensor has already produced its raw output.

- Gyro coefficient: `imu_gyro_bias_random_walk_std_rad_s_sqrt_s`, units rad/s/√s, default 0, bounded 0…0.01.
- Accelerometer coefficient: `imu_accel_bias_random_walk_std_m_s2_sqrt_s`, units m/s²/√s, default 0, bounded 0…0.1.
- At each actual Basilisk scheduler interval `Δt`, each axis receives `Δb = σ_rw * sqrt(Δt) * N(0,1)`. This is an unbounded discrete Brownian bias walk; the coefficient is an engineering assumption, not a measured Allan coefficient.
- Gyro and accelerometer streams use distinct deterministic seeds derived from the user simulation seed. Same input seed and same time grid replay exactly; a different seed changes the bias path.
- Zero defaults do not instantiate the new SysModel and do not alter the historical sensor graph.
- The output message `biasWalkOutMsg` records the random-walk component separately from constant configured bias and event-conditioned bias/noise. The Run Summary identifies the project-owned source, native target properties, seed policy and the absence of installation/hardware calibration evidence.

## Why this is not called an OEM random-walk model

No IMU part number, data sheet, in-flight Allan deviation, temperature sweep, bias-instability coefficient or cross-axis covariance was provided. The coefficient and independent stream derivation are configurable engineering assumptions. The model does not claim a particular device's flicker noise, rate random walk, temperature-dependent drift, cross-axis coupling or sample-clock jitter. DT-3 must establish parameter evidence grades and a task-level uncertainty budget before any physical fidelity claim.

## Required evidence

1. **Native module test:** official Basilisk `ImuSensor` with a constant zero truth message, the project bias-walk SysModel scheduled at priority 91 and the official sensor at priority 90. Verify actual raw `IMUSensorMsg` outputs equal the recorded pre-sensor bias-walk state, and that per-step increment standard deviation agrees with `σ_rw√Δt` within a preregistered statistical tolerance.
2. **Independent streams:** same seed yields bit-for-bit repeat, different seed changes the path, and gyro/accelerometer increment cross-correlation remains within a preregistered bound due to distinct derived seeds.
3. **Whole-spacecraft integration:** enable the walk through the real pre-start TaskSpec, confirm new telemetry channels and summary provenance, repeat the same seed, vary the seed, and combine with official IMU LSB quantization to prove the walk enters before quantization.
4. **Default and negative controls:** omitted/zero fields create no extra module and preserve the baseline; strings, booleans, NaN/Inf, negative and out-of-range coefficients fail closed.
5. **CI:** exact PR HEAD must run this dedicated test step plus all existing IMU white-noise, quantization, ADCS, four-effect, RW, resource, browser and 3600-simulated-second soak regressions. Actual main-push acceptance remains a separate post-merge gate.

## Development lessons

- A bias drift applied after the IMU message cannot be described as pre-ADC sensor bias. Scheduling the project SysModel before the official sensor makes the signal-chain order explicit.
- Use the actual scheduler timestamp to scale random-walk increments by √Δt; never assume a UI sample interval equals the sensor task period.
- Record random-walk state separately from fixed bias, event bias and white noise so that measured telemetry can be attributed.
- Derive distinct gyro/accelerometer random streams instead of assuming the official IMU's single RNGSeed provides independent streams.
- The coefficient is not an Allan deviation or calibrated PSD. Preserve that boundary until independent evidence exists.

## 2026-11-22 integration on actual main (PR #220)

- **Accepted dependency facts**: DT-1 postmerge report exists through #222. #216 native RW deadband/lag and #219 native IMU LSB quantization are already merged. This PR is the *next single DT-2 slice*: seeded gyro/accelerometer bias walk, not a reimplementation of either predecessor.
- **Target base**: `main@4624c3a7df432dc01c8f6df1f479ffc8a196b9ba` (merge of #219); **previous candidate HEAD**: `7242072a1d1bc8f2813375f3277a2d1ca7e1d52b`. GitHub comparison against this exact main produced **9 changed files** (the bias-walk implementation/test/UI/registry/CI/experience set), no duplicate #216/#219 functionality. Prior candidate HEAD had **20 successful PR workflows**, including official Basilisk random-walk A/B. Those results predate this main-ancestor integration and must **not** be relabeled as new-base CI.
- **Integration method**: a true two-parent commit with (1) the existing #220 source HEAD and (2) the exact current main merge SHA, using the existing source tree plus only this experience note. This leaves production physics, stochastic stream definition, tests and numeric thresholds untouched, and makes main an ancestor without force-pushing.
- **Main baseline reacceptance**: the `main@4624c3a7df432dc01c8f6df1f479ffc8a196b9ba` postmerge push workflows for #219 are independently required to finish SUCCESS. At documentation time, some native/browser jobs were still running. A PR candidate is **TECH-CANDIDATE-PASS only after** (a) all relevant main-push workflows complete green, (b) all relevant *new main-based test-merge* PR workflows complete green, and (c) the actual native walk markers/Run Bundle and browser logs are checked; until then **INCONCLUSIVE**.
- **Scientific bounds**: no Allan deviation, sensor manufacturer calibration, flight noise spectrum, actual installation coordinates or hardware seed-replay validation is inferred. `simulation_seed` controls project stochastic streams; the zero-default/Basilisk-native integration contract remains explicit.

### Integration lessons

1. A green stacked-PR build is not a verification of the actual merged main. Retarget first, compare the *true main-to-head* file list, preserve one PR per model slice, then generate a new main-ancestor candidate and require a new test-merge CI.
2. When the main merge tree is represented in a stacked source branch with an unrelated Git ancestry, a two-parent **source-preserving** integration is preferable to force rewriting test history, provided the diff is audited and the merge is genuinely clean.
3. Do not claim a candidate PASS while upstream main push jobs are still in progress. Scientific readiness follows checked raw numeric evidence, not review labels or workflow counts alone.
