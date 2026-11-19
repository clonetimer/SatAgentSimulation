# P10-A4B: Unified-native Payload electrical-load parity

Date: 2026-11-20

Status: MERGED — PR #196 merged to `main` as `2c3f2be10bbd990e725892cce215e2468c1c31f3` (2026-11-20); focused official Basilisk 2.11.0 acceptance passed.

## Objective

Bring the separate agent-exposed `whole_spacecraft.unified_native.v1` runtime into parity with P10-A4A's composite whole-spacecraft Payload electrical-load model. Remove `DataActivityPowerBridge` from this active runtime and use the shared status-only `PayloadActivityStatusAdapter` plus official Basilisk `SimplePowerSink`.

## Model semantics

- Preserve the P10-A4A fixed active-mode load semantics: `payload_power_w` while actual `SimpleInstrument.nodeDataOutMsg.baudRate > 0` and EPS Payload PDU permission is enabled; otherwise 0 W.
- The adapter translates validated instrument/PDU state only; it does not calculate power.
- The official SimplePowerSink output feeds the official battery and the payload thermal node through the same PowerNodeUsageMsg.
- This is an explicit fixed-on engineering profile, not a calibrated payload power curve.

## Implementation

1. Add `PayloadActivityStatusAdapter` after the instrument model and before the native storage/thermal processing stages.
2. Add one official Basilisk `SimplePowerSink` per unified-native spacecraft run, gated by the adapter's output.
3. Connect the same official power output to `SimpleBattery` and the payload thermal input.
4. Replace the legacy bridge health audit with recorder/adapter evidence that validates actual data rate, PDU permission, fixed active load, zero inactive load, and runtime input validity.
5. Preserve the `eps.loads.payload_power_w` telemetry and `payload_power` recorder message contract used by datasets, QoI, Run Bundles and multi-rate recording.
6. Update runtime source-boundary inventories: one fewer engineering proxy, one more official Basilisk node, and one project-owned status adapter.
7. Add paired 38 W / 0 W A/B tests proving battery response, thermal response, unchanged generated data/storage, and PDU load-shed zero-power behavior.
8. Keep the auxiliary heater proxy explicitly classified; it is not part of this slice.

## Acceptance gates

- Official upstream Basilisk 2.11.0, not a mock.
- Unified-native runtime contains no `DataActivityPowerBridge` instance or `project_payload_activity_power_bridge` source entry.
- Runtime manifest includes the instrument-data and PDU inputs to the status adapter, adapter output to official SimplePowerSink, and SimplePowerSink output to battery.
- Actual active instrument data plus PDU permission produces exactly `payload_power_w`; inactive/PDU-off/invalid input produces 0 W.
- Paired 38 W / 0 W runs show the expected battery response after the task feedback boundary.
- The same official power message changes payload thermal temperature/energy in the expected direction.
- Generated data rate and storage accumulation remain unchanged in the power-node-only A/B.
- Proxy inventory decreases from 2 to 1 in the default unified-native runtime; the remaining proxy is the auxiliary heater bridge.
- Existing whole-spacecraft event, thermal, RF, battery, ADCS, Run Bundle and native regression tests remain green.

## Cross-runtime boundary

P10-A4A migrated the composite builder; P10-A4B migrates the unified-native capability. Only after both are merged may the Payload power proxy be described as removed across the two active whole-spacecraft paths.


## Merge and acceptance evidence

- PR #196 squash-merged to `main` as `2c3f2be10bbd990e725892cce215e2468c1c31f3`.
- Focused P10-A run on the accepted PR head: 65 passed in 66.38 s; official upstream Basilisk 2.11.0 provenance and offline environment preflight passed.
- Static checks, project script/dependency governance, unit tests without Basilisk, P9-H truth contract, registered parameter surface audit, and finite-run creator UX regression passed on the accepted head.
- Evidence artifact: `p10a-native-rw-comm-heater-payload-eps-evidence` (Actions run 44569360022).
- Full native Basilisk regression and independent physical/Run Bundle acceptance were still running at merge time. They continue on `main` and are not claimed as passed here.
