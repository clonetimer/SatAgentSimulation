# P10-A2: Native Comm/Data antenna power coupling

Date: 2026-11-19
Status: implementation candidate after P10-A1 merge; scope expanded to preserve both unified and composite runtime power profiles

## Objective

Remove the whole-spacecraft Comm/Data `DownlinkActivityPowerBridge` engineering power proxy by using Basilisk 2.11.0's official `antennaPower.AntennaPower` module, driven by the existing official `SimpleAntenna` log message and actual RF transmit activity.

Keep the RF propagation, link budget, downlink handling and storage path unchanged. This slice is only about the electrical power node and its operational-state adapter.

## Existing graph

- Official `SimpleAntenna` emits `antennaOutMsg` (`AntennaLogMsgPayload` with `antennaState`, `P_Tx`, `P_Rx`).
- Official `DownlinkHandling` emits `DownlinkHandlingMsgPayload` with `linkActive`, `attemptedDataRate`, `deliveredDataRate`, BER and PER. `attemptedDataRate` is the correct TX-power gate: a frame may be transmitted and consume RF power even if BER/PER causes zero successful delivery.
- PDU exposes `commStatusOutMsg`.
- The current project-owned `DownlinkActivityPowerBridge` turns actual downlink activity into a `PowerNodeUsageMsg`, then feeds the battery and thermal network.

## Proposed native graph

1. Instantiate official Basilisk `antennaPower.AntennaPower`.
2. Subscribe `antennaPower.antennaSetStateInMsg` to the existing `spacecraft_antenna.antennaOutMsg`. Do not create a second RF model or alter the link-budget antenna.
3. Add a small project-owned status adapter that reads:
   - `native_downlink.downlinkOutMsg.linkActive`
   - `native_downlink.downlinkOutMsg.attemptedDataRate`
   - `project_eps_pdu.commStatusOutMsg`
   and publishes two `DeviceStatusMsg` outputs: TX enabled only when the PDU permits Comm, `linkActive` is true and `attemptedDataRate` is positive; idle enabled only when the PDU permits Comm and TX is not active. Invalid/unwritten inputs fail closed.
4. Subscribe `antennaPower.nodeStatusInMsg` to that adapter. The adapter translates operational state only; it does not calculate electrical power.
5. Set `antennaPower.basePowerNeed` to the explicit non-RF electronics/PA overhead. With the current antenna `P_Tx=5 W`, preserve both active runtime profiles: unified-native total active draw 16 W = 11 W non-RF base overhead + 5 W antenna TX draw; composite total active draw 30 W = 25 W non-RF base overhead + 5 W antenna TX draw. Validate `downlink_power_w >= P_Tx`; expose the split explicitly rather than silently clipping it.
6. Connect official `antennaPower.nodePowerOutMsg` to the official `SimpleBattery` and the thermal Comm node.
7. Record the native power node output and operational-status evidence. Remove only `project_comm_activity_power_bridge` from the engineering-proxy inventory.
8. Preserve each runtime's legacy idle semantics. Unified-native had `idle_power_w=0`, so no-link or PDU-disabled means 0 W. Composite had `idle_power_w=2 W`; preserve this with an official `SimplePowerSink` gated by the adapter's idle status (2 W only while PDU Comm is enabled and no TX is active). The active `AntennaPower` node is disabled in idle and when PDU is disabled. Do not change the SimpleAntenna state used by LinkBudget; the status adapter is downstream of DownlinkHandling, avoiding an RF-link circular dependency.

## Model semantics and limitations

- `AntennaPower` computes the power draw from `AntennaLogMsgPayload` state: OFF, RX, TX or RXTX, plus `basePowerNeed`; its output is an official `PowerNodeUsageMsg`.
- The status adapter is a state translation layer, not a power model.
- The first candidate is a constant TX-mode draw when an actual transmission is attempted. It intentionally replaces the previous rate-proportional proxy; RF PA dynamic efficiency, output-power control and rate-dependent DC draw remain unmodeled and must be disclosed. A TX attempt with zero delivered rate still consumes TX power because the RF transmission occurred.
- The antenna's RF `P_Tx` remains the value used by the native link budget. Do not replace it with a DC load parameter.
- No RF hardware calibration, BER/PER validation expansion, or flight qualification is implied by this power-node replacement.

## Acceptance gates

- Real public upstream Basilisk 2.11.0 runtime, not mocks.
- Native `SimpleAntenna.antennaOutMsg -> AntennaPower.antennaSetStateInMsg` wiring.
- Unified-native PDU-disabled/no-link cases produce zero draw. Composite preserves 2 W idle draw when PDU is enabled but no TX is active, and zero when PDU is disabled.
- Actual RF transmission (`linkActive` and positive `attemptedDataRate`) with PDU enabled produces the expected TX-mode draw (runtime-specific base overhead + `P_Tx`), even if delivered rate is zero due to BER/PER.
- A/B tests prove the native battery net power and thermal Comm input respond to downlink activation.
- RF link budget, CNR, BER/PER and delivered data remain identical between power-node-off and power-node-on runs (no physics change in the comm link).
- Whole-spacecraft engineering-proxy count decreases from 3 to 2 in both active runtime paths; propulsion-enabled count decreases from 4 to 3. The composite's 2 W idle sink is official Basilisk `SimplePowerSink`, not a new proxy.
- Run Bundle telemetry and manifest record the official module and message connections.

## Upstream Basilisk 2.11.0 references

- `src/simulation/power/antennaPower/antennaPower.cpp`
- `src/simulation/power/antennaPower/_UnitTest/test_unitAntennaPower.py`
- `src/simulation/communication/downlinkHandling/downlinkHandling.h`
- `src/architecture/msgPayloadDefC/DownlinkHandlingMsgPayload.h`
- Release notes identify `simpleAntenna` and `antennaPower` as upstream modules.


## Cross-runtime parity audit added after P10-A1

The original P10-A2 sketch only stated the unified-native 16 W active profile and missed the active composite runtime's legacy 30 W active / 2 W idle profile. The implementation must preserve both profiles explicitly:

| Runtime | Active TX draw | Base overhead | RF antenna TX | Idle when PDU on and no TX | PDU off |
|---|---:|---:|---:|---:|---:|
| unified-native | 16 W | 11 W | 5 W | 0 W | 0 W |
| composite digital twin | 30 W | 25 W | 5 W | 2 W via official SimplePowerSink | 0 W |

The adapter gates on `attemptedDataRate > 0`, not `deliveredDataRate > 0`: failed packets still represent an actual RF transmission. `deliveredDataRate` remains an output/acceptance metric, not the TX power enable condition.
