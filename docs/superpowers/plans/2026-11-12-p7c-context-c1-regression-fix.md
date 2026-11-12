# P7-C C1 regression repair — 2026-11-12

## Baseline and scope

PR #118 baseline `fb1b7f09f2a916e8701f21160fa28a51fe68674b` had five successful workflow runs, one failed run, and one conditionally skipped integration run. The failed run was `43218153922`: finite Basilisk physics passed, but the browser suite ended with 1 failed / 40 passed. The new source-return button omitted the wizard return feedback expected by the existing user journey.

C1 stays in the existing feature branch. C2 result-to-Run-to-diagnostics navigation and P7-D whole-journey acceptance are not claimed as complete.

## Root causes and repair

- The shared-reference updater wrote `activeTaskCenterId`, `activeTaskVersion` and `activeRunId`. Merely inspecting a Goal's base TaskSpec or an experiment comparison could retarget editor saving or Run polling/cancellation. Reference updates now only mutate reference metadata. The editor and Run controllers retain ownership of their own selection.
- Task Center identity and TaskSpec identity are distinct. The strip shows editor Task Center identity/version separately from the inspected experiment/Run TaskSpec, without inferring a saved-task association.
- Task versions are associated with the task ID that was opened or updated; another task cannot borrow that version. A successfully updated saved task refreshes the displayed version.
- Comparison no longer selects the first Run implicitly. Explicit task-run submission retains the original Run-controller assignment. A matching loaded Run is needed before showing its sealed bundle reference.
- Returning via either navigation path clears stale source metadata. Returning to the wizard restores the original feedback. A cancelled realtime departure cannot mutate navigation source metadata: source capture occurs only after the existing leave guard succeeds.

## Test-first evidence

`tests/js/shared_context_vm.js` executes the actual functions extracted from `app.js`, using a small VM harness. Eight behavior cases cover controller isolation, stale references, version association, multi-tool return, ordinary navigation, same-view loops, cancelled realtime departure and reference clearing.

The branch-only checksum-bound repair run `43231125184` first demonstrated the old-source regressions and then passed the same tests against the repaired full source, plus JS syntax, Python syntax and diff checks. Repair source commit: `3248103b0bdb1e12c5199d058678ff21a3d70306`.

The temporary one-shot repair workflow and staging sources are removed in this commit. They do not become application runtime code or ongoing automation. Normal CI retains read-only contents permission.

The existing Chromium assertion was retained. The new behavior wrapper is now included in the normal single-run workbench workflow; final acceptance must use this cleaned-up branch head, not the temporary repair job alone.
