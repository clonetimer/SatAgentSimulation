# P9-N3 Native Thruster Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development to implement this plan; steps below track this bounded deliverable.

**Goal:** Deliver one real native thruster component through the existing TaskSpec and sealed Run Bundle path.

**Architecture:** Add a small native adapter helper beside the existing source-native adapter. Reuse the existing minimal propulsion graph configured for one thruster, and compile the existing periodic command semantics only when they form one continuous burn.

**Tech Stack:** Python, official bsk/Basilisk 2.11.0, existing capability registry and Run Bundle.

**Spec:** docs/superpowers/specs/2026-11-18-p9-n3-native-thruster-design.md

## Global Constraints

- Default project_reference behavior remains available and unchanged.
- No fabricated native measurements, no separate execution or dataset system.
- Native nominal only, one contiguous burn or zero burn; unsupported inputs fail closed before simulation.
- Use official native force and fuel recorders; integrate dense data before sampling output.
- Reuse the existing propulsion builder and component factories; fixture geometry is not installation evidence.
- Official Basilisk 2.11.0 evidence, sealed Run Bundle, latest-head CI, and independent review precede merge.

## Review Focus

- Periodic profile expansion must not silently become a different single pulse.
- Dense native integration must preserve pulses shorter than output sample_s.
- Zero burn is a legitimate successful control despite the old subsystem runner status heuristic.
- Timing validation and scheduling must use the same rounded nanoseconds, including fractional periods.
- Canonical/runtime solver step and native_step_s must agree when both exist; inferred canonical defaults must not be silently ignored. Native seconds/nanoseconds must round-trip exactly before scheduling.
- Fuel exhaustion, nonfinite inputs, and unsupported events must never produce an accepted nominal result.

## Task 1: Native adapter and real physical acceptance

**Files:** Create src/sat_sim/adapters/component_thruster_native.py and tests/test_native_thruster_component.py. Modify only ThrusterAdapter in src/sat_sim/adapters/component_source_native_misc.py, src/sat_sim/capabilities/component.thruster.v1.yaml, and examples/component_thruster_basilisk_native.yaml. Do not edit dictionaries/workflows/docs; root owns those.

**Interfaces:** Native helper consumes the existing runtime spec with flat parameters and returns SimulationResult. ThrusterAdapter delegates only when physics_backend=basilisk_native. Shared validation produces ordinary ValidationIssue errors and is also enforced by run. Add physics_backend (project_reference/basilisk_native, default project_reference) and native_step_s (seconds, default0.01, positive) to capability. Existing trace and summary keys remain complete.

Final-review scope correction: the native-thruster branch of src/sat_sim/task_models.py must preserve the canonical top-level integration step when projecting the runtime mapping, so an explicit conflicting nested solver step cannot hide it. Other capability/reference conversions remain unchanged; include canonical conversion regression coverage.

- [x] Write a failing direct native provenance/physical-response test; run and observe failure on the current reference-only adapter.
- [x] Implement guarded profile-to-contiguous-burn mapping and minimal native execution. Use build_propulsion_basilisk_sim with typed one-thruster configs, execute the SimBase, and inspect strict recorder arrays directly (avoid permissive runner fallbacks).
- [x] Write and verify zero-burn, native force/fuel A/B, dense impulse integration, invalid profile/time/parameter/event tests.
- [x] Add a canonical native example: duration1.0 s, sample0.1 s, native_step0.005 s, thrust1N, Isp280s, profile [0,0.1,0.1,0.1,0.1,0.1,0,0,0,0]. It encodes a single 0.5s burn beginning at0.1s under existing semantics.
- [x] Execute the example through prepare_run/execute_prepared_run; assert SUCCEEDED, PASS, sealed, integrity, native provenance and physical output.
- [x] Verify default project_reference compatibility, self-review, and report changed files/test commands/results. Do not commit/push; root integrates atomically.

Task 1 evidence: official bsk/Basilisk 2.11.0; focused suite 75 passed (73 native-component tests and 2 existing reference-event tests). Canonical TaskSpec 1.0.0 separately passed prepare/execute, validation, sealing and integrity. One-step pulse scheduling and the conservative fuel reserve were established with real native probes. Task-scoped independent review passed after correcting the low-flow numerical resolution and direct-execution identity findings. Final integration review and current-head CI remain gates below.

## Task 2: Governance, experience, and integration

**Files:** Update src/sat_sim/workbench_quantity_dictionary_comm_payload_propulsion.json, relevant parameter display names if required, .github/workflows/p9n3-native-thruster.yml, and the existing P9-N plan/inventory/development-experience docs.

- [x] Record EPS PR171 merge SHA5f294149bfb637117b4fb7772ed6e1053b84f695 and final26/55/457/19 evidence, including recorder-vs-oracle and nanosecond lessons.
- [x] Synchronize thruster parameter/output labels and actual capability blob hash, preserving other dictionary entries and formatting.
- [x] Add focused official bsk2.11.0 CI with explicit provenance check, native acceptance and related reference coverage.
- [x] Independently review Task1 changes; fix substantive findings and rerun only affected verification.
- [ ] Commit the integrated change, create feature PR, verify current-head applicable CI after any review corrections, merge after success, and record the observed outcome.
