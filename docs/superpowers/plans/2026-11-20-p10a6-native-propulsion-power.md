# P10-A6 Native Propulsion Power Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for integration and
> independent scoped implementation/review where useful. Track steps below.

**Goal:** Close propulsion electrical accounting across both active whole-spacecraft runtimes.

**Architecture:** A shared status-only adapter reads each actual native thruster
output; an official SimplePowerSink supplies the electrical message to EPS and
thermal consumers. Recorder totals and health checks remain the evidence source.

**Tech Stack:** Python, official Basilisk 2.11.0, pytest, existing GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-20-p10a6-native-propulsion-power-design.md`

## Global Constraints

- Preserve native thrust, fuel and orbit dynamics and existing output totals.
- Fixed active auxiliary load is an engineering assumption, not calibration.
- Burn-start permission is not an ongoing power-supply switch.
- Use actual official power messages once per consumer; keep the 0.85 heat fraction.
- No scope expansion beyond P10-A propulsion accounting and required acceptance.

## Review Focus

- Permission falling mid-burn must not erase load from a still-active thruster.
- Invalid/unwritten factors must fail closed and be visible in runtime health.
- One inactive thruster must not be charged as though every thruster is active.
- Battery scheduling and coarse recorders must not shift energy or power evidence.
- Zero configured watts must remain valid without making unsupported coupling claims.

## Task 1: Shared adapter and recommended unified-native path

**Files:** `src/integration/resource_feedback.py`,
`src/sat_sim/bsk_engine/unified_native.py`,
`tests/test_native_propulsion_power.py`, `tests/test_propulsion_whole_coupling.py`.

**Interfaces:** `ThrusterActivityStatusAdapter(model_tag, *, thruster_output_msg)`;
`activeStatusOutMsg`; trace fields `time_ns`, `thrust_factor`, `thruster_active`,
`input_status`, `error`. No electrical-power calculation in this adapter.

- [x] Write native adapter and mid-burn permission regression tests; observe RED.
- [x] Implement adapters and per-thruster official sinks; preserve aggregate telemetry.
- [x] Add recorder-driven health/model metadata and per-thruster evidence.
- [x] Verify fixed loads, low SOC, malformed inputs, zero watts and physical A/B.

## Task 2: Composite path parity

**Files:** `src/whole_spacecraft/builder.py`, `src/whole_spacecraft/runner.py`,
`src/whole_spacecraft/schemas.py`,
`src/sat_sim/capabilities/whole_spacecraft.composite_digital_twin.v1.yaml`,
`tests/test_composite_native_propulsion_power.py`.

**Interfaces:** Same shared adapter; component registry exposes
`propulsion_power_nodes`, `propulsion_status_adapters`; recorder names
`propulsion_power_<index>` and `propulsion_active_status_<index>`.

- [x] Write native battery/thermal/unchanged-dynamics A/B tests; observe RED.
- [x] Migrate builder and derive runner totals/energy from official power recorders.
- [x] Verify independent-thruster activity and resource-closure compatibility.

## Task 3: Acceptance, experience and integration

**Files:** Existing P10-A/native/static workflows, inventory assertions,
capability/config descriptions, `docs/development-experience/p10a6-native-propulsion-power.md`.

- [x] Align actual runtime inventories and Run Bundle/browser expectations.
- [x] Run focused official Basilisk tests and relevant repository checks (104 passed; static and governance PASS).
- [x] Obtain independent whole-change review and resolve material findings.
- [ ] Push the feature branch and PR, inspect final-head CI and merge when gates pass.
- [x] Record changes, rationale, pitfalls and actual local verification evidence; remote results belong to the associated PR.
