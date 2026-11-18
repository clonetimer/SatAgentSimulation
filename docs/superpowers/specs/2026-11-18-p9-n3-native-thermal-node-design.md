# P9-N3 official native thermal-node design

Date: 2026-11-18. Continues the accepted P9-N component line after FuelTank PR #173, based on `440b7ebbec37627a007918975ac885fbabe7692a` and the completed [feasibility investigation](../../engineering/p9-n-thermal-native-feasibility.md).

## Outcome and scope

Add opt-in `physics_backend=basilisk_native` to `component.thermal_node.v1`, using one official `Basilisk.simulation.sensorThermal.SensorThermal`. A user can select the native template or switch backend in the existing Workbench, edit its physical parameters, validate, run, inspect temperature curves and export a sealed Run Bundle. The existing `project_reference` first-order model remains the default. Reuse TaskSpec, backend profiles, output binding, quantity dictionary, history and exports.

The native model is a uniform-temperature radiating plate with constant electrical heating and explicitly prescribed fixed Sun, spacecraft state, eclipse and device-status inputs. It does not provide ambient conduction, Earth IR/albedo, thermal networks, propagated orbit, faults, degradation, thermostat, installation geometry or mission coupling. Do not claim high-fidelity or flight validation.

## Parameters and fixture

All values are finite numbers, not Boolean or numeric strings. Native scalar support is deliberately bounded for this first engineering demo; these are adapter acceptance limits, not official SensorThermal limits.

| Parameter | Default | Accepted range / meaning |
|---|---:|---|
| `physics_backend` | `project_reference` | `project_reference` or `basilisk_native`; template selects native explicitly |
| `initial_temp_k` | 300 | 1–2000 K on native; explicitly map `T_0 = K - 273.15` |
| `power_w` | 10 | 0–1e6 W of constant electrical heat |
| `surface_area_m2` | 0.01 | 1e-6–1e3 m² |
| `mass_kg` | 1 | 1e-6–1e6 kg |
| `specific_heat_j_per_kg_k` | 900 | 1–1e6 J/(kg K) |
| `absorptivity` | 0.25 | 1e-6–1 |
| `emissivity` | 0.8 | 1e-6–1 |
| `normal_b` | `[1,0,0]` | Three finite numbers; unit vector within absolute norm tolerance 1e-12 |
| `sun_direction_n` | `[1,0,0]` | Three finite numbers; unit vector within absolute norm tolerance 1e-12 |
| `illumination_factor` | 0 | 0–1; constant eclipse multiplier |

Every native physical field is explicitly set. Connect `SpicePlanetStateMsg` at nominal 1 AU in `sun_direction_n`, `SCStatesMsg` at the origin with zero MRPs, `EclipseMsg` with the requested illumination and `DeviceStatusMsg` with `deviceStatus=1`. Keep the messages alive through execution. These are declared fixture inputs; Sun distance does not introduce inverse-square scaling in the pinned model.

Reference fields `ambient_k`, `tau_s`, `heat_gain_k_per_w`, clipping limits, `power_profile_w`, unknown parameters, non-nominal modes, events, constraints, overrides and required couplings are rejected before native construction. No default-filling, legacy migration or form projection may silently discard an invalid explicit request. Default reference behavior and its supported effects remain intact.

## Scheduling and numerical preflight

Use standard `simulation.step_s`, optional matching `simulation.solver.step_s`, and `simulation.sample_s`. If neither step is supplied, use the sample period. Reject an explicitly invalid time before coercion or defaults. Quantize with the official `sec2nano` rule, require round-trip integer nanoseconds below `2**52`, `sample <= duration`, `step <= sample`, exact complete intervals and at most 100,000 integration steps. Reject incompatible solver methods; the official model uses explicit Euler.

Let `C = mass * specific_heat`, `B = 5.76051e-8 * area * emissivity`, and `Q_hi = power + 1366 * area * absorptivity * illumination * (1 + 1e-12)`. This all-orientation heat-input upper bound does not reconstruct native heat-flow output. Compute an outward-rounded `T_hi = max(initial_kelvin, (Q_hi/B)**0.25)`, require finite positive intermediates and an envelope no greater than 2000 K (allow only outward-rounding tolerance at the exact boundary). For the actual quantized integration step `h`, require `h * 4 * B * T_hi**3 / C <= 0.1`.

This bound makes the Euler update monotone on the positive envelope: its derivative remains positive, its lower boundary cannot map below zero, and its upper boundary cannot increase. It is a conservative preflight condition, not a substitute temperature trajectory or an accuracy claim. Document the computed maximum step/envelope in metadata. Record every native integration tick, check exact timestamps, shape, finite temperatures and positive bounded Kelvin, then decimate to the requested sample grid. Never clamp, fill, interpolate or replace native temperatures. Explicitly check convergence against a finer native step in acceptance.

## Outputs and provenance

Native trace declares only `thermal.node.temp_c`, `thermal.node.temp_k` and `thermal.node.input_power_w` in addition to shared time/sample/label bookkeeping. Celsius is exactly the official `temperatureOutMsg.temperature` recording; Kelvin is the explicit `+273.15` conversion. `input_power_w` is the configured electrical command, not recorded heat flow. Native summary derives `thermal.node.initial_temp_k`, `thermal.node.final_temp_k`, `thermal.node.min_temp_k`, `thermal.node.max_temp_k` and `thermal.node.delta_temp_k` from the validated native records. No measured-temperature sensor, effective tau/gain, safety-limit state or heat-flow measurement is invented.

Summary, run labels and metadata identify the selected backend. Include `native_provenance=Basilisk.simulation.sensorThermal.SensorThermal`, installed Basilisk version and module path, `runtime_truth_status`, message bindings, explicit fixture values, native model constants, conversion, dense sample count/step and field-level sources. Derive temperature extrema from all validated dense records and state this in provenance. Time series shown/exported must be sampled native values.

## Backend contracts and public inputs

Keep the current top-level capability as the reference output/behavior contract. Add native parameter definitions to the catalog and explicit parameter subsets for both `project_reference` and `basilisk_native`. Use selected-parameter `parameter_overrides` for native bounds on common fields, preserving reference bounds. Extend the existing profile selector to apply a default profile when declared, without marking default reference requests as native or newly forbidding reference overrides/output behavior. Existing capabilities without a default profile must behave identically.

Selected parameters, outputs, source binding, consuming/producing fields and limitations must flow through forms, validation, compiler and Run Bundle snapshots. Quantity dictionary audits use the union of declared backend fields with consistent units/types. Native-only fields must not appear as reference form defaults; reference-only fields must not return through native default filling. Backend switching retains common parameters, removes unavailable choices with the existing notice, clears stale execution state, and preserves explicit empty plot selections.

Follow runtime whole-map precedence: nonempty `parameters.values` is authoritative; otherwise use `model.config`. Apply projection edits to that authoritative map. Reject Boolean scalar/vector components before equality-based form deltas can hide `True == 1` or `False == 0`. Cover canonical mappings, typed model bindings, legacy flat/nested parameters and public form/API normalization. Do not loosen existing FuelTank validation while reusing its strict input helpers.

## Acceptance

Official bsk/Basilisk 2.11.0 is the supported native runtime and pinned acceptance environment. Reject a mismatched imported/distribution version before graph construction because the numerical constants and step bound are source-version-specific. Compare returned traces against the actual recorder, including time zero. Verify the existing six-probe physics where applicable: 300 K/10 W eclipse, zero-power cooling, full Sun, increased mass/heat capacity, opposing plate normal and 273.15 K input. Verify invalid numeric/vector/timing inputs, unstable steps, reference-only requests and forged/malformed recorder data are rejected without fallback. Exercise selected contracts and reference compatibility, canonical and legacy execution, schema projection, telemetry fields, exported Python and sealed CSV/integrity. Run a real Chromium template/backend-switch journey in CI. Independent review and current-head required CI gate merging.

## Development record

Record the #173 closure and this batch's official-model observations, numerical boundary, selected-contract lessons and reproducible verification evidence in the existing P9-N experience and capability inventory. Research prose must be updated to distinguish feasibility from delivered native acceptance. Continuous authorization covers implementing, reviewing and merging this bounded next step; no separate planning approval is needed.
