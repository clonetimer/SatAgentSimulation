# P10-A4B Unified-native Payload power — development experience

Date: 2026-11-20

Status: MERGED — PR #196 merged to `main` as `2c3f2be10bbd990e725892cce215e2468c1c31f3`; focused official Basilisk 2.11.0 acceptance passed.

## What changed

- Replaced the unified-native `DataActivityPowerBridge` with the shared `PayloadActivityStatusAdapter` and an official Basilisk `SimplePowerSink`.
- The adapter reads actual `SimpleInstrument.nodeDataOutMsg` and EPS PDU Payload status; it publishes a fail-closed DeviceStatus message and owns no power equation.
- The official SimplePowerSink feeds the battery and the payload thermal-node bridge.
- Unified-native recorder key `payload_power` and trace field `eps.loads.payload_power_w` remain unchanged.
- Payload proxy count is expected to fall from two to one; the auxiliary heater remains an explicit proxy.
- Added a paired 38 W / 0 W test for battery response, thermal response, unchanged data/storage, and PDU shedding.

## Scheduling decision

The instrument is inserted at task priority 40, the PDU at 49, and the mission mode gate at 45. The new status adapter is inserted at priority 39 and the power sink at 38, so the adapter consumes the current tick's instrument data and PDU permission after those publishers have executed. The battery runs earlier at priority 50 and therefore consumes the power-node message on the next tick. Paired battery A/B assertions must compare the following battery sample, not the same-index sample.

## Engineering model boundary

The active payload load is fixed at `payload_power_w`; it is not linearly scaled with baud rate. This matches P10-A4A and avoids retaining an unsupported rate-to-DC-power equation. It is not a calibrated hardware curve.

## Pitfalls to verify

1. Do not connect a second project-owned power equation; the adapter is status-only.
2. Keep the official power node's same output connected to EPS and thermal to avoid split-source accounting.
3. Update runtime manifest and source-boundary counts; otherwise the old proxy may be gone from the graph but still falsely advertised in the audit.
4. Keep the low-SOC PDU transition test: the PDU permit and instrument data rate are independent conditions.
5. The fixed load is a mode assumption. If future hardware evidence supports rate-dependent DC draw, add an explicit measured profile instead of restoring the old proportional proxy.
6. Importability without Basilisk must be preserved. The first PR run exposed that `PayloadActivityStatusAdapter` lacked the `None` placeholder used by the other optional Basilisk classes; add it to the module's no-Basilisk branch and `__all__` so unit-only tools can import the unified runtime.
7. Removing one proxy and adding one official node changes runtime inventories: default unified-native proxy count 2 → 1; official and project-native module counts each increase by one. Update propulsion and sealed Run Bundle assertions to use the new audited counts; the first P10 run's two failures and eight fixture errors were stale inventory expectations, not physical-link failures.

## Acceptance evidence — focused PASS

- Official upstream Basilisk 2.11.0 provenance and offline environment preflight: PASS.
- Focused P10-A suite: 65 passed in 66.38 s, including the unified-native Payload 38 W / 0 W A/B, battery response, thermal response, unchanged data/storage and PDU load-shed behavior.
- Resource-closure health, PDU transition and event regressions in the focused suite: PASS.
- Static checks, project script/dependency governance, unit-only importability without Basilisk, P9-H truth contract, registered parameter surface audit and finite-run creator UX regression: PASS.
- Full native Basilisk regression and independent physical/Run Bundle acceptance were still running at merge time; they continue on `main` and must be checked before declaring the broader regression closed.


## Merge

- PR #196 merged to `main` as `2c3f2be10bbd990e725892cce215e2468c1c31f3`.
- Evidence artifact: `p10a-native-rw-comm-heater-payload-eps-evidence` (Actions run 44569360022).


## Merge

- PR #196 merged to `main` as `2c3f2be10bbd990e725892cce215e2468c1c31f3`.
- Focused P10-A: 65 passed in 66.38 s. Evidence artifact: `p10a-native-rw-comm-heater-payload-eps-evidence`.
- Default unified-native proxy inventory now reports one remaining engineering proxy: the heater status-to-power bridge.
