# P0 Agent input and run consistency

## Approved objective

Implement the first deliverable from the 2026-11-05 SatAgentSimulation automation upgrade proposal: execute the user's latest applied instruction faithfully, bind generated configurations to their successful parse result, and preserve explicitly requested optional parameters during refinement.

## Scope

- Reuse the current Canonical TaskSpec, explicit-parameter/event guards, form projection, task center, and Run Bundle.
- Agent input is cleared only after a successful applied answer. Nonempty input on the Agent tab is pending and must be parsed before Run, even when a previous TaskSpec exists.
- A rejected or superseded answer must not replace the accepted configuration or start a run.
- Add optional source_request_id to POST /runs. When present, verify an existing successful parse report and exact canonical TaskSpec hash before any task/run write. Manual/API submissions without a receipt retain current validation behavior.
- Record accepted source association in the existing Run Bundle input/provenance.json before execution and sealing; preserve TaskSpec bytes and physics execution contracts.
- Refinement may introduce an optional registered parameter missing from the base only when explicitly requested. Keep unrelated base parameters and reject unsupported or mismatched values through existing guards.
- Model/transport fixtures establish interface and control-flow behavior only. No model-quality or native-physics claims follow from these tests.

## Constraints

Python >=3.11,<3.14; preserve Windows compatibility. No new runtime dependency. No changes to model weights, native runners, physical tolerances, recovery guarantees, or historical evaluation scores.

## Deferred stages

P1 experiment tools and evidence analysis, P2 GoalRun orchestration, and P3 knowledge/model routing follow after this independently reviewable P0 slice.

