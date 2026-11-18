# P9-N3 Native Fuel Tank and Backend Contracts

## Goal and scope

Add an honest official Basilisk fuel-tank component through the existing `component.fuel_tank.v1`, TaskSpec, registered adapter and sealed Run Bundle. Keep its project reference pressure model available. A native run must never promise or fabricate pressure. This also establishes the small backend contract projection needed by other components whose official observations differ from their reference models.

The user has authorized continued feature implementation, review, CI and merge. This batch is the independent fuel tank and its necessary contract plumbing. Official thermal-node research is recorded for the next batch; it is not represented as delivered functionality.

## Effective contracts

- The existing top-level capability remains the default `project_reference` contract, with its existing pressure outputs and events.
- Optional `backend_profiles` declares a named backend's parameter allowlist and explicit contract overrides. Only declared, bounded contract sections are projected; never mutate cached registry data or create a second capability ID.
- `basilisk_native` selects parameters `physics_backend`, `capacity_kg`, `initial_mass_kg`, `mass_flow_kg_s`; nominal mode only. Explicit pressure parameters, profiles, events and unknown inputs fail closed, even when they equal reference defaults.
- Native trace fields: `propulsion.fuel_tank.propellant_mass_kg`, `propulsion.fuel_tank.mass_rate_kg_s`, `propulsion.fuel_tank.max_mass_kg`, `propulsion.fuel_tank.commanded_mass_flow_kg_s`. Units are kg, kg/s, kg, kg/s. Native summaries retain final_mass_kg, consumed_mass_kg and initial_mass_kg. The command is labeled input, not a native observation.
- Planning, parameter consumption, compilation, form projection and the Run Bundle contract snapshot use the same effective backend contract. Requested pressure outputs (including plots) fail before execution. Empty requested QoI defaults to the selected backend's summaries.
- Form schemas accept an optional backend selection and expose available backend profiles. The form backend selector must update available parameters/outputs, preserve shared configured values, and make removed incompatible inputs visible to the user. Native template/form defaults contain no pressure fields. Existing capabilities without profiles keep their behavior.
- Quantity-dictionary auditing checks the union of declared profile outputs and detects conflicting units/dtypes, while runtime forms display only the selected output set.

## Native physical graph

Reuse `build_fuel_tank_bundle_from_config` and `attach_fuel_tank_to_spacecraft`: one official `FuelTank` constant-volume state effector attached to an explicit 10 kg free `Spacecraft` carrier. A fixed `MassFlowRateMsg.massFlowRate` is connected to official `fuelLeakRateInMsg` by the existing builder. This is a prescribed mass-withdrawal boundary, not a feed-pressure model, native fault campaign or connected thruster system. Fixed radius/zero-offset fixture geometry must be disclosed and not labeled installation evidence.

Use the official `fuelTankOutMsg` recorder at every integration step. Export mass, signed mass derivative and maximum configured mass directly from the recorder. Compute consumed mass only as the recorded initial minus recorded current mass. Analytical `m0 - flow*t` is an independent conservation check, never a replacement for observations.

## Numerical and failure boundaries

- Use standard `simulation.step_s` / `simulation.solver.step_s`; no second native-step parameter. Legacy flat specs without a step use sample_s. Canonical top-level and solver steps, when both specified, must agree after Basilisk nanosecond rounding and survive runtime projection.
- Duration/sample/step must be finite positive, normalize by Basilisk rounding and round-trip from the scheduled integer nanoseconds to seconds and back without changing the integer grid, with complete duration/sample and sample/step intervals. At most 100000 native integration steps.
- This first backend bounds all scheduling values below 2**52 nanoseconds (about 52 days). It also retains the standard TaskSpec raw ordering step <= sample <= duration before nanosecond normalization, on direct and planned calls alike.
- Capacity must be finite and at least 0.000001 kg (the existing public parameter minimum); initial mass finite in [0, capacity]; commanded flow finite nonnegative. Zero-flow and empty zero-flow controls are valid.
- Positive withdrawal must leave at least one integration step of reserve: `flow*(duration+step) < initial_mass`. Fail closed before depletion, underflow, unrepresentable increments or unresolved positive consumption. A per-step decrement must exceed the accumulated floating-point rounding budget `2*(steps+2)*ulp(initial_mass)`.
- Native mass-withdrawal intermediates require mass*flow to remain finite and normal (not subnormal) from initial mass down through the remaining reserve mass. This is a native arithmetic boundary, independently demonstrated with overflow, underflow and near-limit real-model controls.
- After execution, recorder lengths/times/finite values, mass bounds, signed flow, configured capacity, positive consumption for positive flow, exact constant mass for zero flow and dense mass conservation must be checked. No native dependency fallback.
- Direct adapter.run enforces the same identity and physical boundaries as preflight before building the graph.
- Required mission couplings are unsupported by this independent fixture and must reject on direct and planned calls. Legacy migration must preserve explicit step inputs and reject contradictory identity, unsupported event envelopes and unapplied overrides before it can erase those requests.

## Acceptance

Official bsk/Basilisk 2.11.0 is mandatory. Demonstrate zero flow, empty zero flow, doubled flow, changed initial mass/capacity, fractional grids, sparse exports from dense recorders and invalid boundary cases. Through public planning and prepare/execute prove canonical 1.0 and legacy 0.1 SUCCEEDED, validation PASS, sealed/integrity, native provenance and effective contract snapshots. Reference nominal/fault behavior and all unaffected capability profiles retain existing behavior. Run focused regression and current-head CI, independent task review and whole-branch review before merge. Do not claim full-repository green: the documented pre-existing whole-spacecraft pinned-plan-hash failure is separate.
