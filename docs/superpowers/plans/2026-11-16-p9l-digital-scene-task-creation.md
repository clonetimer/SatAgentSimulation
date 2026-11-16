# P9-L: Digital-scene task creation and execution handoff

**Status:** implementation in progress  
**Branch:** `feat/p9i-birds4-cad-viewer-final-20261114`  
**Scope:** make the satellite digital scene a task-entry and run-observation surface while reusing the existing offline TaskSpec/Run and real-time Basilisk session contracts.

## Product intent

The digital scene must not become a second simulation backend. It is the visual task-entry and monitoring surface for the existing execution paths:

- Offline: capability selection -> form projection -> TaskSpec preflight -> asynchronous Run submission -> sealed Run verification -> result association.
- Timed real-time: supported capability/policy discovery -> session creation -> explicit start/control -> independent telemetry streams -> scene association.
- Continuous real-time: continuous-session policy/configuration -> native session creation -> explicit start/control -> orbit and ADCS polling/join -> scene association.

No path may silently substitute a proxy backend, start a real-time session without user confirmation, or terminate an existing session because the user navigated.

## User flow

1. From the scene, open **创建仿真任务**.
2. Choose **离线仿真任务**, **限时实时仿真**, or **持续实时仿真**.
3. Continue into the corresponding existing guided workspace.
4. Review the selected capability, parameters, time horizon/rate, outputs and event configuration.
5. Run preflight/contract validation.
6. Explicitly submit the offline Run or create the real-time session. Real-time start remains a separate explicit action.
7. Return to the scene. Keep the correct Run/session identity and source contract; never reuse telemetry from a previous source.
8. Show actual run/session state, simulation time, source quality and result availability. Distinguish sealed historical Run data from live data.

## Current implementation

- Scene header exposes a task-creation dialog with the three supported run modes.
- Dialog is keyboard dismissible, focus-restoring and focus-trapped.
- Offline selection routes to the normal wizard flow and retains existing TaskSpec validation and Run execution.
- Timed and continuous real-time selections route to their existing creation workspaces; real-time service availability is checked and no automatic start is performed.
- Existing scene right-side object configuration panel retains the direct validated single-Run path.
- Scene uses the existing independent orbit/ADCS contracts and strict same-simulation-time GOOD-quality association.

## Remaining work

### P9-L1: launch and handoff acceptance
- Chromium test for opening/dismissing the scene dialog and offline wizard handoff.
- Verify real-time entry routes when the interactive service is enabled; verify unavailable-service feedback when disabled.
- Verify an active session is not stopped or duplicated during scene/workspace navigation.

### P9-L2: task/session identity in scene
- Add an explicit current-source strip: offline sealed Run / timed live session / continuous live session.
- Show source ID, capability, simulation time, stream quality and current lifecycle state.
- Switching source must clear stale ADCS values, wheel phase and old track before rendering new source data.

### P9-L3: in-scene run monitoring
- Keep the scene as the active view while presenting a compact configuration/monitoring dialog.
- Reuse existing TaskSpec form projection, validation, submission and session APIs; do not fork validation or execution logic.
- Offline results must appear only after successful Run sealing/integrity verification.
- Real-time telemetry must be tied to the selected session and strict stream contract.

### P9-L4: end-to-end acceptance
- Offline: create -> preflight -> submit -> execute native Basilisk -> seal -> return to scene -> inspect verified Run.
- Timed real-time: create -> explicitly start -> observe native telemetry -> pause/step/control -> stop -> inspect session history.
- Continuous real-time: create -> explicitly start -> orbit/ADCS independent polling -> exact-time GOOD join -> wheel motion -> stop/reopen/reselect without stale-source leakage.
- Run Chromium on Linux, telemetry contracts on Windows/Linux, native Basilisk regression and the relevant P9-C/P9-F/P9-I suites.

## Acceptance gates

- No duplicate TaskSpec or real-time API implementation.
- No automatic real-time start.
- No synthetic telemetry presented as a real run.
- No cross-session, cross-epoch, cross-stream or non-GOOD telemetry association.
- No stale source values after source/session/run changes.
- All three launch choices work from the scene; unavailable services fail with actionable feedback.
- The browser evidence records the selected source and the actual Run/session IDs.

## Non-goals

- Do not treat BIRDS-4 reference CAD as the simulated spacecraft's as-built assembly.
- Do not invent CAD mounting locations or spacecraft hull dimensions.
- Do not make the scene responsible for performance/efficacy evaluation; that remains a separate post-run evaluation workflow.
