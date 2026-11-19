# P10-A4B browser Run Bundle acceptance follow-up

Date: 2026-11-20

Status: P10-A4B physical regression PASS; browser inventory correction superseded by P10-A5.

## Evidence reviewed

PR #196 merged the unified-native Payload power migration. On its accepted PR head:

- Focused P10-A run used official upstream Basilisk 2.11.0 and passed 65 tests in 66.38 s.
- The full native continuous/segmented regression completed successfully.
- The dedicated unified-native Payload 38 W / 0 W A/B passed, including battery and thermal response plus unchanged data/storage.
- The composite Payload SimplePowerSink regression also passed.
- Static checks, governance, unit-only importability, P9-H and finite-run creator UX checks passed.
- The independent native component/subsystem workflow failed only in nine guided whole-spacecraft browser Run Bundle cases. The cases reached the final source-boundary inventory assertion and reported actual proxy counts of 1 (nominal/ADCS events) or 2 (propulsion events), while the test expected 3 or 4.

## Correction and supersession

PR #197 proposed updating the guided browser assertion from 3/4 to 1/2. P10-A5 goes one step further: it removes the remaining unified-native heater status-to-power proxy, so the expected counts become 0 for nominal runs and 1 when propulsion is enabled.

The P10-A5 branch therefore carries the final 0/1 expectation. It also adds `tests/test_guided_task_workspace_browser.py` to both native and static workflow path filters, so future assertion-only changes trigger same-head browser and syntax validation.

No runtime physics, Run Bundle validation rules, or acceptance thresholds are weakened.

## Development experience

- Runtime source-boundary inventory is a public acceptance contract: component migrations must update focused tests, propulsion/event tests, browser journeys and sealed Run Bundle assertions.
- Use exact run summaries to update inventory expectations; do not infer counts from the intended code diff.
- The first P10-A4B browser correction was incomplete because the browser test file was absent from workflow path filters. Verify both the test assertion and the workflow trigger surface.
- Keep physical Basilisk A/B evidence separate from browser acceptance: the physical regression passed; the browser failure exposed stale metadata expectations.

## Final acceptance

- P10-A5 same-head official Basilisk and guided browser workflow: pending.
- Independent native component/subsystem physical and Run Bundle workflow: pending.
