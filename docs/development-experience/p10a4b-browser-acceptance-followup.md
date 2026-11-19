# P10-A4B browser Run Bundle acceptance follow-up

Date: 2026-11-20

Status: narrow acceptance-contract correction; same-head Chromium/Basilisk CI pending.

## Evidence reviewed

PR #196 merged the unified-native Payload power migration. On its accepted PR head:

- Focused P10-A run used official upstream Basilisk 2.11.0 and passed 65 tests in 66.38 s.
- The full native continuous/segmented regression completed successfully.
- The dedicated unified-native Payload 38 W / 0 W A/B passed (1 test), including battery and thermal response plus unchanged data/storage.
- The composite Payload SimplePowerSink regression also passed.
- Static checks, governance, unit-only importability, P9-H and finite-run creator UX checks passed.
- The independent native component/subsystem workflow failed only in nine guided whole-spacecraft browser Run Bundle cases. Each reached the final source-boundary inventory assertion and reported actual proxy counts of 1 (nominal/ADCS events) or 2 (propulsion events), while the test still expected 3 or 4.

## Correction

The unified-native whole-spacecraft runtime now has one remaining engineering proxy in nominal runs: the auxiliary heater status-to-power bridge. A propulsion-enabled run adds the propulsion activity bridge, yielding two. Payload power is no longer a proxy; it is an official Basilisk SimplePowerSink.

Updated the guided whole-spacecraft browser Run Bundle assertion from 3/4 to 1/2. No runtime physics, acceptance thresholds, or Run Bundle validation rules were weakened.

## Development experience

- Runtime source-boundary inventory is a public acceptance contract: any component migration must update focused tests, propulsion/event tests, browser journeys and sealed Run Bundle assertions.
- Use the exact run's observed summary values to update inventory expectations; do not assume the prior migration changed only one test surface.
- The first browser count adjustment was incomplete: the focused test had been corrected but the independent browser suite still carried the old 3/4 expectation. Verify the full test file and the actual failing line, not just the PR patch fragment.
- A test-only PR did not trigger the native browser workflow because `tests/test_guided_task_workspace_browser.py` was missing from that workflow's path filter. Add the test to both native and static workflow path filters so future assertion-only changes receive same-head browser and syntax validation.
- Keep official Basilisk physical A/B evidence separate from browser acceptance. The former passed; the latter exposed a stale metadata expectation, not a physical coupling defect.

## Acceptance still pending

- Same-head official Basilisk + Chromium guided browser Run Bundle workflow.
- Independent native component/subsystem physical and Run Bundle workflow must pass end-to-end after the assertion correction.
