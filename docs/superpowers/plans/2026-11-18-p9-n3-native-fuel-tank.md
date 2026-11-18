# P9-N3 Native Fuel Tank Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan. Steps use checkbox tracking.

**Goal:** Ship the independent official fuel-tank model with truthful backend-specific parameter and output contracts.

**Architecture:** A pure backend-contract projection supplies the existing planner, compiler, form and Bundle snapshot. A small native adapter helper reuses the existing tank factory and official Spacecraft graph. Default project reference behavior remains intact.

**Tech Stack:** Python, official bsk/Basilisk 2.11.0, existing YAML capabilities, Run Bundles and Workbench.

**Spec:** docs/superpowers/specs/2026-11-18-p9-n3-native-fuel-tank-design.md

## Global Constraints

- No fabricated native pressure or formula-generated telemetry; no second task or dataset system.
- Default project_reference behavior remains available. Existing capabilities without profiles keep their behavior.
- Native nominal constant mass withdrawal only, explicit fixture geometry, no pressure/thermodynamics/mission-coupling claims.
- Use official bsk/Basilisk 2.11.0, direct recorder evidence, sealed Run Bundle and current-head CI before merge.
- Reject unsupported inputs before native graph construction; direct run and planned run share identity and physical validation.
- Preserve source contract/dictionary units and immutable cached registry data.

## Review Focus

- Native pressure can reappear through default QoI, wildcard plots, a projected reference form, or a stale model.config mirror; effective contract selection must follow effective runtime parameters.
- Unknown backend, explicit pressure defaults and unsupported events must fail before model construction.
- Canonical/legacy step inference and explicit nested step conflicts must use the same schedule and reject disagreement.
- Empty zero-flow must pass; positive but unresolvable/depleting flow must fail and cannot be masked by finite outputs.
- Switching backend and returning to reference must preserve common parameter values and expose the correct output set without changing other capabilities.

## Task 1: Native graph and direct physical acceptance

**Files:** Create `src/sat_sim/adapters/component_fuel_tank_native.py`, `tests/test_native_fuel_tank_component.py`, `examples/component_fuel_tank_basilisk_native.yaml`. Modify only `FuelTankAdapter` in `src/sat_sim/adapters/component_source_native_misc.py`. Root owns all shared contract/schema/UI/docs files.

**Interfaces:** `validate_native_fuel_tank(spec: Mapping[str, Any]) -> Sequence[ValidationIssue]`; `run_native_fuel_tank(spec: Mapping[str, Any]) -> SimulationResult`. Runtime parameters are flat. Adapter selects project_reference by default and delegates for basilisk_native. Root supplies effective capability projection and native YAML contract; coordinate via the spec. Helper may import `get_task_capability` from `sat_sim.capability_backends` for selected contract/output validation after it exists.

- [x] Write and run a failing native provenance/recorder-response test on the reference-only adapter.
- [x] Implement one 10 kg carrier plus factory FuelTank with fixed mass flow, strict time/quantity/identity validation and recorder checks from the spec. Preserve reference run path.
- [x] Test official zero/empty/doubled-flow/mass-capacity A/B and dense-to-sparse sampling. Explicitly verify no pressure outputs and field-level provenance.
- [x] Test bad capacity/mass/flow, pressure params/profiles/events, step conflicts, nonfinite and unrepresentable grids, depletion/resolution budget, wrong target and absent dependency without native fallback.
- [x] Create canonical example duration1s, sample0.1s, step0.01s, capacity3kg, initial2kg, flow0.001kg/s, native mass-only QoI/plots. Verify prepare/execute PASS/sealed/integrity after Task2 integration; legacy omitted-step zero flow also succeeds.
- [x] Self-review and report exact commands/results in the SDD report. Do not commit/push; root integrates scoped files atomically.

## Task 2: Backend contract, product defaults and integration

**Files:** Create `src/sat_sim/capability_backends.py` and `tests/test_capability_backend_profiles.py`. Modify `src/sat_sim/capabilities/component.fuel_tank.v1.yaml`, `execution_planner.py`, `task_compiler.py`, `parameter_consumption.py`, `run_bundle.py`, `task_models.py`, `form_schema.py`, `api.py`, `capability_registry.py`, `task_validator.py`, `unified_agent.py`, `scenario_templates.py`, `web/app.js`, `workbench_quantity_names.py`, `workbench_quantity_dictionary_comm_payload_propulsion.json` as necessary for the bounded contract path. Update existing P9-N experience/inventory/plan and add `.github/workflows/p9n3-native-fuel-tank.yml`.

**Interfaces:** `select_capability_backend(contract: CapabilityContract, physics_backend: str | None = None) -> CapabilityContract`; `get_task_capability(spec: Mapping[str, Any], capability_id: str | None = None) -> CapabilityContract`. Selection uses effective runtime parameters (canonical values override model.config). `capability_form_schema(capability_id, *, physics_backend: str | None = None)` returns the selected defaults and a profile catalog without changing calls that omit the keyword.

- [x] Add failing planning/form/snapshot tests that native defaults contain no pressure and explicit pressure QoI/plots/parameters reject; cached base contract and default reference remain unchanged.
- [x] Implement bounded profile projection and connect existing planning/compilation/audit/snapshot consumers. No relaxation of validation_outcome.
- [x] Add native contract YAML, dictionary union audit, selected form schema/API/backend switch and native scenario template; verify default reference and round-trip forms.
- [x] Preserve top-level standard step in native fuel-tank canonical runtime projection so explicit conflicts are observable. No thruster migration behavior changes.
- [x] Run focused native, profile/planning/forms/dictionary/compatibility regression. Resolve concrete failures only; retain the documented unrelated pinned hash issue.
- [x] Record #172 final merge/CI evidence and this batch's measured behavior/limitations. Review the task diff independently and fix substantive findings.
- [ ] Freeze integrated tree, publish PR, independent whole-branch review, verify current-head CI, merge, verify identical merge tree and report actual results.

Local evidence before final integration: official bsk/Basilisk2.11.0 native suite64passed, backend-profile tests29passed, publicAPI roundtrip1passed and referenceevents2passed (combined96passed). Task1 independent SPEC/QUALITY approved after fixing native mass-times-flow overflow/underflow, directcoupling requirements, legacy inputloss and rawtimeordering. Fresh standard compatibility/parameter-audit regression125passed; publicAPI/formprojection/parameterpresentation/RW-template regression28passed. All24quantitydictionary source blobs and union bindingsverified. Actual Chromium tests remain a CI gate: local process start was denied its Unix socket capability. Prior whole-spacecraft pinned-hash fixture failure remains separately documented; no full-repository all-green claim.
