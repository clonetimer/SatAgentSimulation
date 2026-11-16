# P9-K Strict Multi-Stream Telemetry Time Association

> **For agentic workers:** Use test-driven development. Preserve the existing branch and PR; do not merge until all acceptance gates pass.

**Goal:** Drive the engineering digital satellite from separate native Basilisk orbit/attitude and ADCS telemetry streams, joined only on exact simulation time within the same session and epoch.

**Architecture:** Keep `whole.orbit` as the atomic spatial-state quality domain. Add `whole.adcs` as an independent quality domain containing only fields actually emitted by the native whole-spacecraft runtime (initially RW0–RW2 speeds). In the browser, maintain a bounded pending-frame join keyed by exact numeric `sim_time_s`, gated by session ID, stream IDs, UTC epoch and N_INERTIAL frame contract; never interpolate, nearest-neighbor match, or promote INVALID/SUSPECT/GAP frames. A matched pair enriches the existing orbit point with ADCS measurements. Unmatched samples remain pending only within a bounded window and expire without fabricating a pair.

**Tech Stack:** Existing Basilisk 2.11 native runtime, FastAPI interactive session catalog, telemetry bus/WebSocket, vanilla JavaScript scene renderer, pytest, Node test runner, Chromium CI.

**Spec:** User-approved P9-K direction in conversation: independent `whole.adcs` stream and strict time association with `whole.orbit`.

## Global Constraints
- No synthetic Python orbit or sensor values.
- Preserve `whole.orbit` schema and its GOOD quality contract.
- `whole.adcs` fields must exist in native runtime output; initial fields are `adcs.rw.speed_rad_s_0..2`.
- Join requires identical session ID, UTC epoch, N_INERTIAL frame, and exact `sim_time_s`.
- Do not interpolate, extrapolate, or nearest-neighbor join.
- Reject non-GOOD frames, duplicate/out-of-order sequence, mismatched epoch/session, non-finite values and stale unmatched samples.
- Keep source stream, sequence, simulation timestamp and quality metadata per channel.
- UI must show unmatched/stale/quality state and freeze the affected wheel animation.
- Physical configuration remains default; engineering exploded view remains display-only.

## Review Focus
- Orbit and ADCS frames arrive in either order: pair only on exact time.
- Same timestamp from another session/epoch: reject.
- Different sample rates or missed sequence: no fabricated pair; mark gap and freeze affected wheel.
- INVALID/SUSPECT/GAP quality: do not drive motion.
- Zero speed is valid data; missing field is not zero.

---

### Task 1: Native stream contract

**Files:** Modify `src/sat_sim/interactive/api.py`; test `tests/interactive/test_p9f_live_publication.py`.

- [ ] Assert continuous session catalog includes distinct `whole.orbit` and `whole.adcs` streams at identical configured sample period.
- [ ] Assert orbit fields remain spatial state only.
- [ ] Assert ADCS stream contains only native runtime RW speed fields initially.
- [ ] Start real Basilisk session and verify both streams publish GOOD frames at exactly matching `sim_time_s` values with same session ID.
- [ ] Implement the additional stream in the continuous session TaskSpec, preserving independent quality.

### Task 2: Strict browser time join

**Files:** Modify `src/sat_sim/web/satellite-scene.js`, `src/sat_sim/web/app.js`; test `tests/js/test_p9k_telemetry_time_join.cjs`.

- [ ] Test both arrival orders, exact match, one-nanosecond/different-float mismatch, wrong session, wrong epoch, wrong frame, non-GOOD quality, duplicate sequence and bounded pending eviction.
- [ ] Implement bounded exact-time join with a maximum of 256 pending frames per stream.
- [ ] Expose catalog-declared `whole.adcs` subscription and route only its frames to the join.
- [ ] On successful pair, attach typed ADCS values and their independent source metadata to the orbit point.
- [ ] On missing/gapped pair, mark ADCS unavailable and freeze wheel phase; never affect orbit position/attitude rendering.

### Task 3: Native browser evidence

**Files:** Modify `tests/test_p9f_native_browser.py`, `.github/workflows/p9f-telemetry-globe.yml` only if path triggers are needed.

- [ ] Assert real native browser receives GOOD orbit and ADCS frames for same session and exact simulation time.
- [ ] Assert UI displays wheel speeds from ADCS stream while globe position/attitude remains sourced from orbit stream.
- [ ] Assert mismatch and missing-frame cases do not rotate wheels.
- [ ] Preserve real browser evidence and screenshots.

### Task 4: Verification and handoff

- [ ] Run focused Node and pytest tests.
- [ ] Run full P9-F Ubuntu/Windows contracts and native-live Chromium bounded/continuous tests.
- [ ] Run P9-C scene regression, native Basilisk suite, and existing P9-I CAD acceptance.
- [ ] Record run IDs, artifacts and exact evidence; do not merge if any required job is skipped or failing.

### Native browser evidence extension

P9-F product-browser acceptance requires GOOD `whole.orbit` and `whole.adcs` frames from the UI-created session and at least one exact `sim_time_s` pair with matching session ID. The catalog alone is not accepted as evidence of published matching frames.


### Root-cause finding and correction (2026-11-16)

The first real-browser P9-K attempt failed even though both WebSocket streams were present and had matching GOOD frames. The join's catalog selector used the first stream containing the three RW speed fields. Catalog order places `whole.fast` before `whole.adcs`; `whole.fast` is a mixed-quality stream and is INVALID when unrelated propulsion fields are missing. Thus the browser buffered every orbit frame while never subscribing the join to the dedicated ADCS quality domain. This was a stream-selection bug, not a timestamp mismatch or missing Basilisk data.

Correction: `sceneAdcsStream()` now requires the exact stream ID `whole.adcs` in addition to the required ADCS fields; it does not fall back to a mixed stream even if that stream contains a superset of the same fields. Added Node contract tests for both cases: prefer exact `whole.adcs` when `whole.fast` appears first, and return no ADCS stream if the dedicated stream is absent. This preserves the quality-domain boundary instead of weakening GOOD-quality checks.

Commit `817809304dbe18dceb36e886eb30f02a3c2fef64` adds the failing selector contract tests; `210978d1086e804fe000c0cb75eb62584f0f5657` applies the minimal selector fix. Full P9-F native-browser rerun is the acceptance gate.


### Successful-pair observability

On a successful exact-time pair, the scene inspector now replaces the pending status with the dedicated ADCS stream ID, GOOD quality, exact simulation time, ADCS sequence, and a sequence-gap indicator when applicable. This makes the successful association visible to operators and gives the native-browser acceptance a user-visible assertion; it does not change acceptance rules.


---

## P9-L: native ADCS sensor telemetry in the physical scene (2026-11-16)

### Scope
Extend the already validated continuous `whole.adcs` quality domain from wheel speed alone to actual Basilisk recorder outputs. Do not substitute configuration values for measurements.

Both bounded WebSocket sessions and continuous REST sessions declare the same dedicated `whole.adcs` telemetry contract. The stream now declares:
- RW state speed, measured speed, command torque, applied torque and effective torque limit for the active wheel topology.
- IMU measured angular rate and acceleration.
- Star-tracker measured quaternion and explicit device-validity flag.
- Magnetometer three-axis measurement.
- Coarse-sun-sensor output, direction and explicit device-validity flag.

The scene adapter preserves native source fields and creates presentation aliases only at the display boundary. Sensor validity flags remain independent of stream transport quality: a structurally GOOD frame may correctly report an instrument as invalid.

### Physical semantics
- `whole.orbit` remains the exclusive source for position and rigid-body attitude.
- `whole.adcs` remains the independent source for sensor and actuator telemetry.
- Only exact same-session, same-epoch, same-frame and same-simulation-time pairs are associated.
- RW phase integration uses measured state speed; command torque and applied torque are displayed separately from effective torque limit.
- On stream loss or non-GOOD quality, ADCS values are cleared; stale sensor values must not persist in the inspector.

### Added acceptance
- Real Basilisk continuous-session test asserts catalog fields and actual finite IMU, star-tracker, magnetometer and CSS values.
- Real Chromium acceptance checks both bounded and continuous user-created sessions, including bounded TaskSpec stream declarations and visible sensor rows from actual telemetry.
- Four-wheel test checks RW4 state, measured speed and applied torque fields.
- Node tests verify native field-to-inspector mapping and independent star/CSS validity.
- P9-F Ubuntu/Windows and native-browser jobs are the acceptance gate; the catalog declaration alone is not evidence of actual sensor publication.

### P9-C scene interaction defect
A native Chromium regression found the scene shell's stacking context intercepted pointer events over the right-hand object configuration panel. The correction places the panel above the scene layer and isolates its stacking context. Acceptance must use a normal Playwright click on the object tree, not `force=True` or DOM event injection.

Latest implementation commits:
- `d79db4b`: publish native ADCS sensor and actuator telemetry.
- `a17a609`: map native measurements into scene inspector channels.
- `00e2643`: distinguish RW state, measured speed, command, applied torque and limit.
- `9244feb`: restore object-panel hit testing above scene overlays.

The latest P9-F/P9-C reruns remain mandatory before declaring P9-L or P9-C closed.
