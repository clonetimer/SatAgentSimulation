# Browser acceptance development experience: guided workbench and asynchronous validation

Date: 2026-11-16  
Scope: P9-L4 real Chromium + official Basilisk acceptance, PR #151.

## Case 1 — A field with the right data-path can still be the wrong UI control

### Symptom
The P9-L4 offline journey timed out while filling `[data-path="simulation.duration_s"]`. The locator resolved to a real input, but that input belonged to the advanced creator's `#dynamicForm`, which is intentionally hidden while the guided wizard is active.

### Root cause
The product has two representations of the same configuration:
- guided wizard controls, rendered into the active guided dialog (e.g. `data-wizard-field`);
- advanced creator form controls, rendered in `#dynamicForm`.

Matching a shared semantic data path does not establish that a control is visible or belongs to the active user journey.

### Corrective rule
For browser acceptance, identify controls by both semantic identity and active workflow ownership:
1. Assert the active view and wizard step.
2. Locate the guided control using `data-wizard-field` inside the guided workspace.
3. Assert it is visible and enabled before interaction.
4. Exercise the same visible control a user would use.
5. Never bypass the product UI with `force=True`, hidden DOM mutation, or direct API submission when the test claims to verify the user journey.

## Case 2 — Re-rendered controls invalidate previously held locators

### Symptom
After correcting the hidden-control selector, the next CI attempt failed while reading the duration input after TaskSpec validation.

### Root cause
The test retained a locator for a dynamic wizard control and read it inside an assertion-diagnostics dictionary. Validation triggers rendering and may replace the control node. A locator is a query, not a persistent DOM element reference; reusing it after a state transition may resolve against a replaced or no-longer-mounted node.

### Corrective rule
Capture user-entered values immediately after the interaction and before triggering any state transition that can re-render the view:
- record the value in a local immutable test variable;
- put that captured value in the evidence report;
- use the captured value in assertion diagnostics;
- reacquire a fresh locator after a re-render only if the UI is expected to retain that control.

## Case 3 — Assertion diagnostics must not mask the original failure

### Symptom
The failure appeared as a 30-second timeout in `Locator.input_value()`, obscuring whether TaskSpec validation itself had succeeded.

### Root cause
The test accessed a stale locator while constructing the assertion's diagnostic payload. The diagnostic evaluation raised a second exception, replacing the original validation failure.

### Corrective rule
- Diagnostics must be side-effect-free and must not depend on stale locators.
- Snapshot relevant UI values before asynchronous calls and render transitions.
- On validation failure, capture the notice, validation summary, selected capability, submitted configuration snapshot, page errors, and screenshot before raising.
- Wait for an explicit terminal validation state, not a fixed sleep and not a success-only phrase that can hang when validation fails.
- Distinguish backend validation failure from browser locator/test-harness failure.

## Case 4 — Keep evidence layers separate

P9-L4 has three distinct evidence classes:
1. Guided UX tests with fixture-backed API responses prove UI navigation and interaction only.
2. Real finite Run acceptance must prove Chromium-originated `POST /runs`, the existing execution queue, official Basilisk execution, sealed Run integrity, and scene restoration for the same Run ID.
3. Bounded and continuous real-time acceptance must prove browser-created sessions, real Basilisk lifecycle and telemetry provenance.

Do not count fixture-backed journeys as physics evidence. Do not mark a mode PASS based only on an API preflight, a scene render, or an older commit's CI.

## Reusable checklist

- [ ] Locator belongs to the currently active UI workflow, not a hidden parallel editor.
- [ ] Visibility and enabled state are asserted before interaction.
- [ ] Input values are snapshotted before validation/navigation/re-render.
- [ ] Async operations wait for terminal success or failure state.
- [ ] Failure diagnostics cannot throw and mask the primary failure.
- [ ] Product UI -> API -> execution engine -> persisted evidence -> scene replay is verified end to end where claimed.
- [ ] Fixture, preflight, native physics and visual evidence are explicitly distinguished.
