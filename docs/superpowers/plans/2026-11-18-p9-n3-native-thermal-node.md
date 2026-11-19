# P9-N3 Native Thermal Node Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver official recorder-backed SensorThermal through the existing component TaskSpec, Workbench and sealed Run Bundle.

**Architecture:** Keep the project reference backend as default and route explicit native requests to a bounded official SensorThermal graph. Apply selected parameter and output profiles throughout the existing pipeline. Extend strict public-input checks already proven by FuelTank without changing unrelated task behavior.

**Tech Stack:** Python, official bsk/Basilisk 2.11.0, Pydantic, YAML/JSON contracts, pytest, existing FastAPI/JavaScript Workbench and Chromium CI.

**Spec:** `docs/superpowers/specs/2026-11-18-p9-n3-native-thermal-node-design.md`

## Global Constraints

- Official temperature recordings only; Kelvin is an explicit Celsius conversion, never a replacement temperature model.
- Existing project reference behavior remains default; no second task system, mission coupling, CAD or unimplemented thermal physics.
- Native fields, bounds, fixture and step criterion are exactly those in the design tables and scheduling section.
- Preserve runtime whole-map parameter precedence, explicit invalid inputs and explicit empty output selections.
- No fabricated native tau/gain, measured temperature, safety state or heat-flow outputs.
- Merge only after independent review and applicable current-head CI; record development evidence.

## Review Focus

- A default profile must hide native-only parameter defaults without activating native-only restrictions on reference requests (Task 1 contract test).
- Pre-coercion checks must reject Boolean times, physical scalar/vector components and nested/config-backed edits that compare numerically equal (Task 2 input tests).
- Coarse export sampling must not hide invalid intermediate native temperatures (Task 1 forged recorder test).
- Valid fields in sealed telemetry must contain real Celsius/Kelvin data, not a time-only CSV (Task 2 Bundle test).
- Solver/standard step declarations and unsupported reference fields must not be erased by legacy migration or form normalization (Task 2 public API tests).

### Task 1: Official graph, numerical boundary and backend contract

**Files:** Create `src/sat_sim/adapters/component_thermal_node_native.py`, `tests/test_native_thermal_node_component.py`, `tests/test_thermal_node_backend_contract.py`; modify only the thermal class in `src/sat_sim/adapters/component_source_native_misc.py`, `src/sat_sim/capabilities/component.thermal_node.v1.yaml`, and `src/sat_sim/capability_backends.py`.

**Interfaces:** Produce `validate_native_thermal_node(spec: Mapping[str, Any]) -> Sequence[ValidationIssue]` and `run_native_thermal_node(spec: Mapping[str, Any]) -> SimulationResult`. Reuse generic `NativeComponentInputError`, `validate_native_component_timing(simulation, *, require_duration_and_sample=False)` and `validate_native_component_events(spec, *, component_name='native component')` from `sat_sim.task_models` (root supplies these backwards-compatible names). Existing Fuel aliases continue working. Use the spec's exact parameter/output names.

- [x] Write failing recorder-provenance, exact grid/unit-conversion, default/reference profile isolation and invalid-boundary tests; record the red result.
- [x] Add default profile parameter filtering while preserving existing no-default-profile behavior and reference restrictions. Declare native-only outputs and sources as whole-section replacements.
- [x] Implement official graph, strict native preflight, analytical envelope/step bound, dense recorder checking, sampled exports and truthful field provenance. Route validate/run without fallback.
- [x] Verify native A/B power/illumination/mass/cp/orientation responses, zero-power cooling, time-zero 273.15 K, finer-step convergence and tampered intermediate recorder rejection.
- [x] Run `PYTHONPATH=src:. .../eps-venv/bin/python .superpowers/sdd/p9n3-native-thermal-node/run_pytest.py -q tests/test_native_thermal_node_component.py tests/test_thermal_node_backend_contract.py tests/test_capability_backend_profiles.py tests/test_native_fuel_tank_component.py`; expected no failures.
- [x] Write task report and review package; obtain independent spec and quality review.

### Task 2: Public input, Workbench, sealed delivery and release evidence

**Files:** Modify `src/sat_sim/task_models.py`, `src/sat_sim/unified_agent.py`, `src/sat_sim/scenario_templates.py`, `src/sat_sim/workbench_quantity_dictionary_eps_thermal.json`; add `examples/component_thermal_node_basilisk_native.yaml`, focused thermal form/legacy/API/Bundle/browser tests and `.github/workflows/p9n3-native-thermal-node.yml`. Also update `src/sat_sim/script_exporter.py` for the observed selected-source/default-alignment defect and `src/sat_sim/validation/parameter_surface_audit.py` plus its tests for the observed default-profile coverage mismatch. Only change other form/compiler/UI code if an observed thermal regression requires it. Update P9-N experience, inventory, feasibility and existing plans.

**Interfaces:** Consume Task 1's selected capability and adapter functions. Generic timing/event helpers preserve Fuel aliases. Template id `component_thermal_node_basilisk_native`; native example duration 10 s, step/sample 0.1 s, defaults from the spec (eclipse baseline).

- [x] Add meaningful failing tests for raw canonical/legacy/form timing, forbidden inputs/output fields and Boolean physical quantities; cover values-backed and config-backed projection plus inactive config precedence.
- [x] Extend strict input checks to the two profiled native components. Validate before coercion, alias selection, default filling and equality-based projection; retain reference behaviors and task identity.
- [x] Add the example/template, Chinese parameter/output names and source blob evidence; verify selected schemas contain only valid fields. Common-value retention on real browser backend switching is part of the CI gate below.
- [x] Execute canonical/legacy examples through Run Bundles, confirm sealed CSV values/units, telemetry content and integrity, and check exported Python. Add the actual-browser CI journey using established Workbench selectors.
- [x] Run focused thermal tests plus Fuel input/profile, parameter audit, component effect and existing Workbench/TaskSpec regressions relevant to touched paths. Do not claim a full-repository test pass.
- [ ] Record #173 closure and this batch's actual results/lessons. Obtain final whole-branch spec/quality review, resolve findings, publish PR and wait for applicable exact-head CI including Chromium.
- [ ] Merge the authorized feature with expected-head protection; verify merged tree equals reviewed/tested tree, preserve CI/review evidence in PR, and clean only this task's temporary workspace.


## Local acceptance before final review and CI

Official bsk/Basilisk 2.11.0 core/contract/FuelTank evidence: **175 passed**, no skips. Integrated native thermal, public inputs, delivery, profile, Fuel input, reference effects and parameter-surface audit: **272 passed**, one pre-existing Starlette deprecation warning. Canonical and legacy sealed Run Bundles and their telemetry CSV values/integrity were verified; both exported Python scripts ran the official model and reproduced the recorded baseline. All 24 quantity dictionary source blobs matched their capability files. Browser results and exact final head/merge-tree evidence are release gates recorded in the feature PR, not inferred from local API tests.

After correcting the newly exposed default-profile parameter-surface audit mismatch, the same targeted compatibility command completed **76 passed** (TaskSpec compatibility, structural parameter audit/presentation, lossless form projection, reference component effects and exported plan identity). It overlaps the focused suite; counts are not added.

Task 1 independent SPEC/QUALITY review is approved with zero open findings after a scoped correction. Final corrected integrated local suite: **275 passed in 31.16s**, one baseline Starlette deprecation warning. The three new cases reject missing/noncallable native write timestamps and enforce native-specific conformance metadata.

## Whole-branch review correction

PR #174's first head passed the official thermal gate (275 cases and 2 Chromium journeys), but the Agent gate rejected the default example because it included native-only catalog parameters. The independent whole-branch review also reproduced nested-legacy export provenance, native export coercion and duplicate-form Boolean alias gaps. All four findings were corrected in one wave, with 16 failing regression cases before implementation. The selected-backend contract now drives prompt examples and summaries; explicit native selection survives compact examples. Export metadata comes from the embedded canonical payload, and strict native export/CLI input preserves invalid literals for formal validation. Raw aliases receive Boolean type checks before equality comparisons.

The correction suite passed 175 cases. After adding thermal subsystem and whole-spacecraft routing controls, the final Agent and local thermal export identity scope passed 29 cases. Valid native numeric edits and reference kW unit aliases retained their established behavior. The final updated thermal CI command passed **313 cases in 46.74s** locally, with one baseline Starlette warning; JavaScript syntax verification passed. These scopes overlap. Scoped review disposition, corrected-head CI and merge-tree identity are recorded in PR #174; the initial head is not merge evidence.
