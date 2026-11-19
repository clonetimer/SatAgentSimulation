# P10-A1B: ADCS power parity for the active composite whole-spacecraft capability

Date: 2026-11-19
Status: implementation candidate in PR #192; same-head native A/B pending

## Why this exists

P10-A1A replaces the ADCS power proxy in the recommended `whole_spacecraft.unified_native.v1` runtime. A source audit found a second active, agent-exposed whole-spacecraft capability:

- `whole_spacecraft.composite_digital_twin.v1`
- Lifecycle: active; exposed to agent and real LLM evaluation.
- Runtime: `src/whole_spacecraft/builder.py` + `src/whole_spacecraft/runner.py`.
- It still instantiates `AdcsControlPowerBridge` from the pre-unified graph.

This path is not historical report data and must not be silently counted as migrated or deprecated. P10-A1A alone does not claim global removal of the ADCS power proxy; this P10-A1B slice extends the migration to the active composite path.

## Objective

Bring the active composite capability into physical-interface parity with P10-A1A, or retire it only after an explicit feature-parity and compatibility decision. Preferred first option: replace its custom ADCS power bridge with official Basilisk per-wheel `ReactionWheelPower` nodes.

## Implementation outline

1. Inspect `adcs_graph.rw_closed_loop.rw_state_effector.rwOutMsgs` and use one official `Basilisk.simulation.ReactionWheelPower` per physical wheel.
2. Subscribe each node to the actual official RW output message (`Omega`, `u_current`), not `rwMotorTorqueOutMsg` from the FSW command path.
3. Subscribe the official node status input to the existing EPS PDU ADCS status message.
4. Feed every native node output to the existing official `SimpleBattery` and thermal-network ADCS node.
5. Preserve the legacy composite capability's explicit parameter semantics. Do not silently reuse constants or change mission/thermal behavior; introduce a clear power-profile schema if needed.
6. Replace `resource_feedback_bridges["adcs"]` and `component_registry["adcs_power_bridge"]` consumers with a structured native per-wheel power-node collection and a summary/telemetry aggregator.
7. Update `whole_spacecraft.runner` and `integration.qoi_profiles.resource_closure` so QoI evidence is computed from native power-node outputs and actual RW state messages.
8. Keep the legacy capability lifecycle active until its feature parity is verified. If the product chooses to retire it instead, set an explicit replacement to `whole_spacecraft.unified_native.v1` only after browser, task-template, real-LLM-eval and fault/degradation coverage has been checked.

## Acceptance

- Official public Basilisk 2.11.0 only; no mocks.
- Nominal, zero-current, current-limited and ADCS-PDU-disabled A/B.
- Native per-wheel `u_current` and `Omega` reproduce official `ReactionWheelPower` outputs.
- Native battery net power and thermal ADCS node respond to the same official power messages.
- Resource-closure QoI still distinguishes idle vs slew and uses direct native recorder evidence.
- Composite Run Bundle / dataset / browser / agent-eval contracts remain valid.
- The active composite capability reports no ADCS engineering power bridge; remaining payload, comm, heater and propulsion proxies remain explicitly counted.
- No flight validation or motor winding calibration claim.

## Known legacy coupling consumers to audit

- `src/whole_spacecraft/builder.py`: creates the old ADCS power bridge and feeds battery + thermal.
- `src/whole_spacecraft/runner.py`: extracts `adcs_power_bridge.trace` and recorder field `adcs_power`.
- `src/integration/qoi_profiles/resource_closure.py`: reads `component_registry["adcs_power_bridge"]` for idle/slew power evidence.
- `src/sat_sim/capabilities/whole_spacecraft.composite_digital_twin.v1.yaml`: active lifecycle and agent/evaluation exposure.

## Boundary

P10-A1B is a separate migration slice. It must not be represented as already completed by P10-A1A's PASS.
