# DT-2 / IMU native quantization — implementation and evidence ledger

**Status:** candidate, unmerged. The evidence gates below require execution on the exact PR HEAD; this file records design scope, not a pre-declared physics PASS.

## Why this slice

R1 requires sensor-level nonidealities without replacing the working Basilisk whole-spacecraft integration. Existing main already has native ImuSensor constant bias, seeded gyro/accel Gaussian white noise, body-to-platform rotation, position and saturation. **Do not reimplement them.** Missing explicitly user-selectable measurement quantization is addressed independently of #216 reaction-wheel deadband/lag, #207 reference-only CAD and #215 inconclusive GMAT comparison.

## Source and physical boundary

- Official Basilisk v2.11.0 upstream source (pinned release commit `6c1511f5385dccaa73c9ae24522a74adc24fe623`), `src/simulation/sensors/imuSensor/imuSensor.cpp`: `setLSBs(double LSBa, double LSBo)` takes **acceleration first, angular velocity second**. `applySensorDiscretization` applies `aDisc.discretize` and `oDisc.discretize` to the official IMU measurements, and adjusts native delta-V/delta-angle internal outputs.
- New opt-in `imu_gyro_quantization_lsb_rad_s` (0…0.1 rad/s) and `imu_accel_quantization_lsb_m_s2` (0…1 m/s²), each 0=disabled. At least one positive LSB invokes the official `imu.setLSBs(accel_lsb, gyro_lsb)`; both omitted/zero **do not call** it, preserving the historical model.
- Invalid values (nonfinite, negative, out-of-range, string, bool and null) must fail at both the guided preflight and direct native runtime; no silent coercion.
- New LSB values are **engineering assumptions** only. Neither the CAD geometry nor the spacecraft inertia defines the IMU resolution. No real sensor datasheet, calibration, flight-level precision, anti-alias filter, sample-timing jitter, or noise-density-vs-bandwidth calibration is claimed.
- This is **native output quantization** and not a separate Python curve-rounding layer. The pre-existing seeded stochastic model stays in official ImuSensor.

## Reproducible validation protocol

The new `tests/test_native_imu_quantization.py` requires official Basilisk native whole-spacecraft runs, not test doubles of the physics module:
1. With identical explicit input biases, compare old omitted LSB parameters versus explicit zero: all recorded raw gyro/accel samples must match to the predeclared 1e−12 tolerance, with no new summary evidence.
2. Enable both LSBs (`0.01 rad/s`, `0.02 m/s²`): *actual* official `IMUSensorMsg.AngVelPlatform` and `AccelPlatform` outputs must lie on the corresponding digital lattices within 1e−8 **LSB units**, and deviate nontrivially from the unchanged baseline. Both measured deltas must be printed.
3. Combine opt-in quantization with existing official white-noise PMatrices and `simulation_seed=811`: same seed => replay of raw IMU time series, changed seed `812` => observable stochastic difference. LSB lattice remains intact.
4. Strict native runtime and guided preflight must reject invalid LSB values, including NaN/Inf and numeric strings or booleans.
5. The registered capability, guided ADCS form, browser controls and static parameter surface audit must expose both fields with the same units and defaults.
6. Native CI `.github/workflows/continuous-satellite-native.yml` runs these tests as an independent step, preserving all previously required regression/soak tests; actual post-merge `main` CI remains a separate gate.

Printed measurement marker names: `DT2_NATIVE_IMU_QUANTIZATION_AB_MEASURED` and `DT2_NATIVE_IMU_QUANTIZED_SEEDED_REPLAY_MEASURED`. Values are **not** stated here before the real CI emits them.

## Engineering lessons

- Reuse upstream `setLSBs`: its argument order is easy to invert; verify against pinned native C++ source instead of estimating from the parameter names.
- Treat instrument resolution and noise standard deviation as different parameters; measured white-noise PSD has **not** been independently identified.
- A floating-point direct field accepts numeric strings in default Pydantic mode; a `before` validator is needed for fail-closed typed engineering input.
- Do not declare a test PASS from a source tree or a previously green candidate; require same-head real Basilisk output, then a separate actual main-push acceptance after approval.
- Keep drift/correlated noise, bit-depth-derived LSB, temperature error, and cross-tool external truth for subsequent DT-2 / DT-3 slices rather than asserting completeness from a quantization-only A/B.
