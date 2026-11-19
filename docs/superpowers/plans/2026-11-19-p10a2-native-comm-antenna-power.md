# P10-A2: Native Comm/Data antenna power coupling

Date: 2026-11-19
Status: implementation committed on feat/p10a2-native-comm-antenna-power-20261119; same-head Basilisk integration acceptance pending

## Objective

Remove the whole-spacecraft Comm/Data `DownlinkActivityPowerBridge` engineering power proxy by using Basilisk 2.11.0's official `antennaPower.AntennaPower` module, driven by the existing official `SimpleAntenna` log message and actual RF transmit activity.

Keep the RF propagation, link budget, downlink handling and storage path unchanged. This slice is only about the electrical power node and its operational-state adapter.

## Existing graph

- Official `SimpleAntenna` emits `antennaOutMsg` (`AntennaLogMsgPayload` with `antennaState`, `P_Tx`, `P_Rx`).
- Official `DownlinkHandling` emits `DownlinkHandlingMsgPayload` with `linkActive`, `attemptedDataRate`, `deliveredDataRate`, BER and PER. `attemptedDataRate` is the correct TX-power gate: a frame may be transmitted and consume RF power even if BER/PER causes zero successful delivery.
- PDU exposes `commStatusOutMsg`.
- Before P10-A2, the whole-spacecraft runtime used a project-owned `DownlinkActivityPowerBridge` to turn actual downlink activity into a `PowerNodeUsageMsg`, then feed the battery and thermal network.

## Proposed native graph

1. Instantiate official Basilisk `antennaPower.AntennaPower`.
2. Preserve the physical `SimpleAntenna.antennaOutMsg` and its existing connection to LinkBudget. Do not alter the RF antenna or create a second RF propagation model.
3. Extend the project-owned `DownlinkAntennaStatusAdapter` to read:
   - `native_downlink.downlinkOutMsg.linkActive`
   - `native_downlink.downlinkOutMsg.attemptedDataRate`
   - `project_eps_pdu.commStatusOutMsg`
   - the physical `SimpleAntenna.antennaOutMsg` (`P_Tx/P_Rx`)
   It publishes TX/idle `DeviceStatusMsg` outputs and a separate power-only `AntennaLogMsg` copy. The copy reports TX only when PDU permission, `linkActive` and `attemptedDataRate > 0, physical antenna state is TX/RXTX and P_Tx > 0 are all true; otherwise it reports OFF. The original SimpleAntenna message remains untouched for LinkBudget.
4. Subscribe official `AntennaPower.antennaSetStateInMsg` to the adapter's gated antenna-log copy. Set `AntennaPower.basePowerNeed=0`; this node accounts only for physical antenna RF power.
5. Represent non-RF electronics/PA overhead with a separate official PDU/TX-gated `SimplePowerSink`: 11 W unified-native and 25 W composite. Preserve total TX profiles: 16 W unified-native = 5 W RF + 11 W base; 30 W composite = 5 W RF + 25 W base.
6. Connect both official power nodes to the official `SimpleBattery` and the thermal Comm node. The composite's separate official 2 W idle `SimplePowerSink` remains enabled only while PDU Comm is on and no TX is active.
7. Record the antenna RF node, base node, idle node, adapter states and source/message manifest. Remove only `project_comm_activity_power_bridge` from the engineering-proxy inventory.
8. Preserve no-link and PDU-disabled zero-load behavior. A failed packet attempt still consumes TX power because the gated antenna log remains TX when `attemptedDataRate > 0`, even if delivered rate is zero.


## Implementation progress on the current branch

- Unified-native now uses official `Basilisk.simulation.antennaPower.AntennaPower` fed by a power-only gated copy of the physical `SimpleAntenna.antennaOutMsg`; the original message continues unchanged to the native RF LinkBudget.
- Non-RF base overhead is a separate official PDU/TX-gated `SimplePowerSink` (11 W unified-native, 25 W composite). Composite preserves its separate official 2 W PDU-enabled idle sink.
- The status adapter gates TX from PDU permission and the actual `DownlinkHandling` attempted-rate signal. It does not calculate electrical power.
- The summary health audit reports stimulus, PDU transition and directional-response evidence. The complete t=0 reset/scheduler barrier is excluded from causal power metrics; every positive-time sample is strictly checked.
- Composite trace output records `comm_control_power_w`; unified trace output exposes PDU status, TX/idle status, attempted rate and total native Comm load. Same-head paired RF/battery/thermal tests and capability/CI updates remain the acceptance gates.


## Model semantics and limitations

- Official `AntennaPower` computes the RF antenna load from the gated `AntennaLogMsgPayload` state and physical `P_Tx/P_Rx`; `basePowerNeed` is zero in this integration.
- The status adapter copies only antenna state and physical RF power to a separate power-side message. It never changes the original `SimpleAntenna` message or the RF LinkBudget path.
- Non-RF electronics/PA overhead is represented by an official `SimplePowerSink`, gated by the adapter's TX status. Composite idle is a third official `SimplePowerSink`.
- The model is a constant TX-mode draw. RF PA efficiency, output-power control and rate-dependent DC draw remain unmodeled. A TX attempt with zero delivered rate still consumes TX power because an RF transmission was attempted.
- The antenna's RF `P_Tx` remains the value used by the native link budget; it is not treated as a DC bus measurement.
- No RF hardware calibration, BER/PER validation expansion, or flight qualification is implied.


## Acceptance gates

- Real public upstream Basilisk 2.11.0 runtime, not mocks.
- Physical `SimpleAntenna.antennaOutMsg` still feeds LinkBudget unchanged and also feeds the status adapter; the adapter's gated power-side `AntennaLogMsg` feeds official `AntennaPower`.
- Unified-native TX=16 W (5 W AntennaPower + 11 W base sink), idle/no-link/PDU-off=0 W.
- Composite TX=30 W (5 W AntennaPower + 25 W base sink), PDU-enabled idle=2 W, PDU-off=0 W.
- Actual RF transmission (`linkActive` and positive `attemptedDataRate`) with PDU enabled produces the expected TX-mode draw even if delivered rate is zero due to BER/PER.
- Paired A/B tests prove the native battery net power and thermal Comm input respond to downlink activation; antenna RF, base and idle node contributions are independently recorded.
- RF link budget, CNR, BER/PER and delivered data remain identical between power-node-on and power-node-off runs.
- Whole-spacecraft engineering-proxy count decreases from 3 to 2 in both active runtime paths; propulsion-enabled count decreases from 4 to 3.
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


## Physical antenna state validation refinement

The power-only TX gate additionally requires the real SimpleAntenna state to be TX or RXTX. A PDU-enabled downlink attempt must not create an electrical TX load when the physical antenna is OFF or RX-only. The adapter continues to leave the original RF antenna log untouched; its separate power-side log is OFF in this case.


## Same-task scheduling and idle-state acceptance refinement

- Composite `ReactionWheelPower` nodes are inserted at priority 0 after the spacecraft/RW effector models and before recorders. The PDU remains priority 49; the power nodes must consume current-tick `Omega/u_current`, not the previous tick.
- Idle draw is verified in a separate real no-TX run by setting the physical SimpleAntenna to OFF. The actual SimpleAntenna/LinkBudget/DownlinkHandling chain must then report no attempt while PDU remains enabled; the official idle sink must draw 2 W.


## Power-audit sampling semantics

- The pure Comm health audit converts adapter `time_ns` using `1e-9 s/ns`; it must not depend on a function-local Basilisk `macros` import.
- In the composite graph, SimpleBattery is scheduled before the downstream PDU/Comm power nodes. The battery balance therefore reflects the prior sample's native PowerNodeUsage outputs; A/B battery assertions compare the next sample, while RF and power-node telemetry remain same-sample comparisons.


## Final inventory and terminal battery sample

- The project-native module inventory includes the project-owned Comm status adapter: 28 with propulsion enabled and 27 without; official Basilisk unique-module counts are 29 and 27.
- Battery A/B comparisons use the next sample after each TX power-node output because the battery executes earlier in the task. The terminal TX sample is excluded only when no next battery sample exists; at least one following sample remains mandatory.
