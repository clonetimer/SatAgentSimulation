# Thermal native feasibility reconnaissance

Date: 2026-11-18. Feasibility evidence against baseline `2cd59c2f7a494e33c70de6fff6d4cb7a6df23e99`, using official bsk/Basilisk 2.11.0. The original probes below established feasibility, independently of production acceptance. Follow-on implementation status is recorded separately in the next section.

## Follow-on native TaskSpec integration (2026-11-18)

The P9-N3 follow-on slice now implements the opt-in official SensorThermal adapter under `component.thermal_node.v1`, with explicit native/reference parameter subsets and native output/source contracts. The [design](../superpowers/specs/2026-11-18-p9-n3-native-thermal-node-design.md) defines the conservative physical range and Euler step condition. `examples/component_thermal_node_basilisk_native.yaml` runs the 300 K / 10 W eclipse baseline. Canonical and legacy solver-only tasks have completed real official bsk 2.11.0 sealed Run Bundles, including the final Celsius/Kelvin CSV values, prefix-selected slower telemetry and integrity checks. Canonical and legacy capability-python exports also execute the official model and reproduce 300.06961637618775 K at 10 s.

The native trace uses `thermal.node.input_power_w` for the configured electrical command, not the historical reference `power_w` field. Both `temp_c` and converted `temp_k` originate in the official recorder. No measured-temperature alias, tau, gain, heat-flow reconstruction or thermal safety label is advertised. Fixed status is on; zero configured power covers electrical-heating off. The independent review, current-head CI, browser and merge evidence are recorded by the [implementation plan](../superpowers/plans/2026-11-18-p9-n3-native-thermal-node.md) and its feature PR. The feasibility probe results below remain historical evidence rather than a substitute for those release gates.

## Recommendation

An honest official-native thermal component is feasible now: one official `Basilisk.simulation.sensorThermal.SensorThermal`, static explicitly declared Sun / spacecraft / eclipse / device-status messages, and its `temperatureOutMsg` recorder in one `SimBaseClass`. The smallest implementation is nominal, constant electrical heating, fixed attitude and Sun direction, and one radiating plate. It is **not** a drop-in physical implementation of the existing first-order thermal-node contract.

Keep the project reference path as the default. An opt-in `physics_backend=basilisk_native` path can live under `component.thermal_node.v1` only if both parameter applicability and output applicability are explicit per backend. If the registry/form/export layer cannot represent that distinction without advertising unsupported outputs, a separate plate-thermal capability is more honest than filling native `effective_tau_s` / `effective_heat_gain_k_per_w` with invented values.

## Existing repository model and builder evidence

- `src/sat_sim/capabilities/component.thermal_node.v1.yaml` currently advertises first-order `ambient_k`, `tau_s`, `heat_gain_k_per_w`, `initial_temp_k`, `power_w`, and `power_profile_w`. The schema is a project deterministic model with `backend_type: orchestration_wrapper`, not an official Basilisk model.
- `src/components/thermal_node/builder.py::step_thermal_node` computes `a = min(1, dt/max(tau,1e-12))`, `target = ambient + heat_gain*power`, and `T_new = clip(T + a*(target-T), min_temp, max_temp)`. Defaults are ambient 300 K, tau 10 s, gain 1 K/W; the adapter starts at 300 K and applies 10 W unless overridden.
- `src/sat_sim/adapters/component_source_native_misc.py::ThermalNodeAdapter` is entirely project Python. It derives measured temperature by adding a bias, and implements runaway, blocked path, resistance growth, and capacity drift by changing input power, tau, or gain. These existing effects have no native implementation merely by choosing a Basilisk scheduler.
- Both `src/components/thermal_node/builder.py::ThermalNodeScheduledSysModel` and `src/components/thermal_node/basilisk_scheduled.py::ThermalNodeScheduledSysModel` are Python subclasses of `SysModel`. The latter explicitly says it is not a native Basilisk thermal network. Their conduction/thermostat model uses heat input, heat capacity, ambient conductance, heating, and cooling.
- `src/subsystems/thermal/builder.py::build_thermal_basilisk_sim` instantiates that project `ThermalNodeScheduledSysModel`, not `SensorThermal`. Its recorder messages are safe/heater/cooling status, and its source map identifies the project class. Reusing it does not satisfy official-native temperature evidence.
- `src/sat_sim/bsk_engine/thermal_environment.py::NativeThermalEnvironment` reads native messages but performs the repository's environmental heat computation for a project network. It does not instantiate `SensorThermal`.
- `src/integration/resource_feedback.py::PowerToThermalAggregationBridge` can set an existing `SensorThermal.sensorPowerDraw` from power messages. It is an explicit Python orchestration bridge, not a native thermal integrator. The constant-power component should omit this extra bridge initially.

### Why there is no equivalent tau/gain mapping

For positive gain and tau, the repository's unclamped continuous first-order equation could be interpreted as conductance `G = 1/gain` and heat capacity `C = tau/gain`. This is an interpretation of that linear model, not an official `SensorThermal` mapping. `SensorThermal` instead radiates with a fourth-power temperature term and has no ambient temperature or conduction input. Its local time constant changes with temperature. Initial temperature and electrical power transfer directly; ambient, tau, gain, clipping, and their modifiers do not.

## Installed official runtime and verified units

Runtime: isolated Python 3.12 environment with the official `bsk==2.11.0` distribution.

Runtime check: `importlib.metadata.version('bsk') == '2.11.0'`. Module locations:

- `Basilisk/simulation/sensorThermal.py`
- `Basilisk/simulation/_sensorThermal.so`

Installed SWIG evidence is explicit: `messaging.TemperatureMsgPayload.temperature.__doc__` returns `[Celsius] current temperature`. The official pinned header declares `T_0` as Celsius and the temperature output payload as Celsius. Native runs below preserve `T_0=26.85` at time zero and `T_0=0` at time zero, further confirming the input/output mapping. Use `T_0 = initial_temp_k - 273.15`; expose raw recorder values as `thermal.node.temp_c` and convert recorded Celsius to Kelvin for `thermal.node.temp_k`. Do not send Kelvin into `T_0`, and do not treat recorder Celsius as Kelvin.

### Public configuration (verified installed attributes and pinned header)

| Official field | Physical meaning/unit | Required validation | Suggested TaskSpec field |
|---|---|---|---|
| `T_0` | Initial temperature, Celsius | Finite, greater than absolute zero | `initial_temp_k` with explicit conversion |
| `sensorPowerDraw` | Electrical power converted to heat, W | Finite, nonnegative | Existing scalar `power_w` |
| `sensorArea` | One radiating plate area, m² | Finite and strictly positive | `surface_area_m2` |
| `sensorMass` | Thermal mass, kg | Finite and strictly positive | `mass_kg` |
| `sensorSpecificHeat` | Specific heat, J/(kg K) | Finite and strictly positive | `specific_heat_j_per_kg_k` |
| `sensorAbsorptivity` | Solar absorptivity | Strictly `0 < value <= 1` | `absorptivity` |
| `sensorEmissivity` | Infrared emissivity | Strictly `0 < value <= 1` | `emissivity` |
| `nHat_B` | Plate outward normal in body frame | Finite unit vector; reject zero | `normal_b` |
| `sensorPowerStatus` | Multiplier for electrical heating, off/on | Exactly 0 or 1 | Fix at 1 initially; power=0 covers no heating |

Native `Reset` rejects zero absorptivity and emissivity even though high-level prose can look like an inclusive [0,1] interval. It normalizes a normal only when its norm exceeds 0.1, and logs an error otherwise. Adapter-side strict validation should precede native construction instead of relying on native logging or silently repairing invalid vectors.

Installed constructor defaults were area/absorptivity/emissivity −1, mass 1 kg, specific heat 890 J/(kg K), `T_0=30` Celsius, power 0 W, and power status 1. Documentation examples mentioning an initial default of 0 Celsius are stale relative to the installed pinned source; set every physical field explicitly.

### Message connections

| Input/output | Message type | Exact fields needed in minimal fixture |
|---|---|---|
| Required `sunInMsg` | `SpicePlanetStateMsg` | `PositionVector`, a nonzero Sun position relative to the prescribed spacecraft position |
| Required `stateInMsg` | `SCStatesMsg` | `r_BN_N`, `sigma_BN` |
| Optional `sunEclipseInMsg` | `EclipseMsg` | `illuminationFactor`, finite [0,1] |
| Optional `sensorStatusInMsg` | `DeviceStatusMsg` | `deviceStatus`, exactly 0/1 |
| Output `temperatureOutMsg` | `TemperatureMsg` | `temperature`, Celsius |

For the first component slice, connect all four inputs explicitly. Prescribe `r_BN_N=[0,0,0]`, `sigma_BN=[0,0,0]`, Sun position `AU*1000*unit(sun_direction_n)`, and constant `illumination_factor`. This is declared fixture geometry, not a propagated orbit, SPICE ephemeris, or dynamic eclipse. Keep message objects alive until the simulation and recorder finish. Add the official model before its recorder with explicit priorities. No project temperature writer is needed.

## Version-specific physics and limits

The pinned 2.11.0 C++ source uses explicit Euler integration of electrical heating plus absorbed Sun power minus emission, with thermal capacity `mass*specific_heat`. It computes projected area from the body normal and Sun direction, clamps negative projection to zero, and uses `T_c+273.15` in emission.

Two implementation constants matter: fixed solar flux `S=1366 W/m²`, and emission constant `5.76051e-8 W/(m² K⁴)`. These differ from `astroConstants.SOLAR_FLUX_EARTH=1361` and the usual reference Stefan–Boltzmann value. Preserve the official model's output and document its constants; do not replace temperatures or build an oracle from a different model. The source computes Sun direction but **does not implement inverse-square Sun-distance scaling**. Restrict the fixture claim to fixed nominal 1 AU.

The official documented scope is a uniform-temperature plate with insulated backing: solar absorption, radiative emission, and electrical power fully converted to heat. It does not model spacecraft conduction, Earth albedo, arbitrary thermal networks, or radiator/heater control. No Earth IR input exists. The internal `Q_in`, `Q_out`, and constants are private, and the only public output message here is temperature; do not label analytical heat-flow reconstruction as native recorded heat flow.

## Executed real-model evidence

Executed six fresh native `SimBaseClass` graphs using only official `SensorThermal` for temperature integration. Every graph used `duration=10 s`, task step `0.1 s`, 101 recorder samples at `0, 0.1, ..., 10 s`, area 0.01 m², absorptivity 0.25, emissivity 0.8, specific heat 900 J/(kg K), body normal +X, identity attitude, Sun +X at 1 AU. Unless varied: mass 1 kg, initial 26.85 Celsius (300 K), electrical input 10 W, device status 1, eclipse illumination 0. All recorded samples were finite.

| Case | Recorded first sample, °C | Recorded next sample, °C | Recorded final sample, °C | Final K (unit conversion only) |
|---|---:|---:|---:|---:|
| Eclipse, 10 W | 26.85 | 26.85069635439111 | 26.9196163761878 | 300.06961637618775 |
| Eclipse, 0 W | 26.85 | 26.84958524328 | 26.80853567791544 | 299.9585356779154 |
| Full Sun, 10 W | 26.85 | 26.851075798835556 | 26.957550429514473 | 300.10755042951445 |
| Eclipse, 10 W, double mass | 26.85 | 26.850348177195556 | 26.884812953944326 | 300.0348129539443 |
| Eclipse, 10 W configured, status off | 26.85 | 26.84958524328 | 26.80853567791544 | 299.9585356779154 |
| Eclipse, 10 W, initial 0 °C | 0 | 0.0008260662394749378 | 0.08258955276828525 | 273.2325895527683 |

For the baseline, the first step exactly follows the official source equation: `26.85 + 0.1*(10 - 0.01*0.8*5.76051e-8*300**4)/900`. Turning off the device exactly matches zero electrical heating; doubling mass halves the first increment; full Sun adds absorbed power `1366*0.01*0.25 = 3.415 W` at the first step. These checks exercise actual native recorder output, not a Python replacement thermal model.

## Smallest integration contract and acceptance gates

1. Route only explicit `basilisk_native`; keep reference behavior available and identify both paths in summary/metadata. Give the physical parameters above explicit native applicability. Provide a native template that omits reference-only defaults; a compiler must not silently inject ambient/tau/gain into a strict native allowlist.
2. Initially allow nominal mode, constant nonnegative `power_w`, fixed geometry, and constant illumination. Reject `ambient_k`, `tau_s`, `heat_gain_k_per_w`, `min_temp_k`, `max_temp_k`, `power_profile_w`, faults, degradation, constraints, events, and unknown parameters on native runs, including callers bypassing TaskSpec validation.
3. Native trace should contain native Celsius, converted Kelvin, time/sample index, and explicitly identified input power. Native qoi can derive initial/final/min/max temperature and temperature change from recorder values. Do not fabricate tau, gain, a separate measured temperature, safety-limit state, or native heat-flow samples. If `measured_temp_k` must be kept, explicitly declare it an ideal alias of recorded truth with no temperature-sensor model; a separate official `tempMeasurement` integration would be additional scope.
4. Use a bounded integration cadence, validate nanosecond representability and exact duration/sample alignment, and reject non-finite/physically invalid recorder results. The existing broad capability minimum of 0.001 s alone is not an Euler-stability guarantee for arbitrary area/mass/cp values. Choose conservative physical parameter limits or an explicit stability bound before broadening inputs; sampling convergence is a meaningful acceptance check.
5. Include provenance for `sensorThermal.SensorThermal`, installed Basilisk version/module path, message types, constant fixture inputs, exact Celsius→Kelvin conversion, official numerical constants, and all unsupported physics. Declare `runtime_truth_status` as actual native temperature recording.
6. Tests should cover unit conversion with 0 °C/273.15 K, nonconstant recorded temperature, power and eclipse response, mass/cp response, cooling without power, invalid physical values/vectors, unsupported reference fields/modifiers, exact sample grid, and a sealed Run Bundle whose claimed temperature channels come from the native recorder. Native heating/cooling and its timestep behavior are material risks; broad unrelated tests are unnecessary for feasibility.

## Primary-source evidence

Pinned official sources were retrieved directly over HTTPS with Python `urllib.request.urlopen` because the search tool's raw-GitHub opener returned a disabled-fetch error. Content hashes below fix the inspected text. No source was modified or copied into production.

- [Basilisk v2.11.0 sensorThermal.cpp](https://raw.githubusercontent.com/AVSLab/basilisk/v2.11.0/src/simulation/thermal/sensorThermal/sensorThermal.cpp), SHA-256 `f6d60f64f59c9b164a8718c8c3d071d43a7261ffd47f60cac181fc7f2a4b13d5`. This is the exact-version authority for initialization, validation, constants, message reading, temperature update, and lack of distance scaling.
- [Basilisk v2.11.0 sensorThermal.h](https://raw.githubusercontent.com/AVSLab/basilisk/v2.11.0/src/simulation/thermal/sensorThermal/sensorThermal.h), SHA-256 `0459dd05dc2536ac7ce51b064a841fb0f74f87b9efe7d16f8b1136beb41ac13f`. Public units/API and private heat-flow state.
- [Basilisk v2.11.0 TemperatureMsgPayload.h](https://raw.githubusercontent.com/AVSLab/basilisk/v2.11.0/src/architecture/msgPayloadDefC/TemperatureMsgPayload.h), SHA-256 `c56ad04d4ecc7688e4752e240dbd067b938a1caa5e11f06761d5ce64abe6ce12`. Celsius output, also independently visible in the installed SWIG property docstring.
- [Official sensorThermal documentation](https://hanspeterschaub.info/basilisk/Documentation/simulation/thermal/sensorThermal/sensorThermal.html), retrieved 2026-11-18, page identifies 2.4.0; supports physical model scope, insulated backing, and no albedo. Use pinned source rather than this older page for defaults and exact-version implementation.
- [Official development sensorThermal documentation](https://avslab.github.io/basilisk/develop/Documentation/simulation/thermal/sensorThermal/sensorThermal.html), retrieved 2026-11-18, page identifies 2.13.0b0; independently confirms Celsius and message topology but is not the pinned runtime version.
