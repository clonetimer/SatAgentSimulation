# P10-A2: Native Comm/Data antenna power coupling

Date: 2026-11-19
Status: next vertical slice; implementation starts after P10-A1 same-head full regression and merge

## Objective

Remove the whole-spacecraft Comm/Data `DownlinkActivityPowerBridge` engineering power proxy by using Basilisk 2.11.0's official `antennaPower.AntennaPower` module, driven by the existing official `SimpleAntenna` log message and actual downlink activity.

Keep the RF propagation, link budget, downlink handling and storage path unchanged. This slice is only about the electrical power node and its operational-state adapter.

## Existing graph

- Official `SimpleAntenna` emits `antennaOutMsg` (`AntennaLogMsgPayload` with `antennaState`, `P_Tx`, `P_Rx`).
- Official `DownlinkHandling` emits `DownlinkHandlingMsgPayload` with `linkActive`, `deliveredDataRate`, `attemptedDataRate`, BER and PER.
- PDU exposes `commStatusOutMsg`.
- The current project-owned `DownlinkActivityPowerBridge` turns actual downlink activity into a `PowerNodeUsageMsg`, then feeds the battery and thermal network.

## Proposed native graph

1. Instantiate official Basilisk `antennaPower.AntennaPower`.
2. Subscribe `antennaPower.antennaSetStateInMsg` to the existing `spacecraft_antenna.antennaOutMsg`. Do not create a second RF model or alter the link-budget antenna.
3. Add a small project-owned status adapter that reads:
   - `native_downlink.downlinkOutMsg.linkActive`
   - `native_downlink.downlinkOutMsg.deliveredDataRate`
   - `project_eps_pdu.commStatusOutMsg`
   and publishes a `DeviceStatusMsg` that is enabled only when the PDU permits Comm and a real downlink is active with positive delivered rate.
4. Subscribe `antennaPower.nodeStatusInMsg` to that adapter. The adapter translates operational state only; it does not calculate electrical power.
5. Set `antennaPower.basePowerNeed` to the explicit non-RF electronics/PA overhead. With the current antenna `P_Tx=5 W` and total active `downlink_power_w=16 W`, the initial accounting candidate is 11 W base overhead + 5 W antenna TX draw. Validate `downlink_power_w >= P_Tx`; expose the split explicitly rather than silently clipping it.
6. Connect official `antennaPower.nodePowerOutMsg` to the official `SimpleBattery` and the thermal Comm node.
7. Record the native power node output and operational-status evidence. Remove only `project_comm_activity_power_bridge` from the engineering-proxy inventory.
8. Preserve the current no-link and PDU-disabled zero-load behavior. The official RF link-budget path remains untouched, avoiding a circular dependency between antenna state and link availability.

## Model semantics and limitations

- `AntennaPower` computes the power draw from `AntennaLogMsgPayload` state: OFF, RX, TX or RXTX, plus `basePowerNeed`; its output is an official `PowerNodeUsageMsg`.
- The status adapter is a state translation layer, not a power model.
- The first candidate is a constant TX-mode draw when an actual downlink is active. It intentionally does not preserve the previous rate-proportional proxy; RF PA dynamic efficiency, output-power control and rate-dependent DC draw remain unmodeled and must be disclosed.
- The antenna's RF `P_Tx` remains the value used by the native link budget. Do not replace it with a DC load parameter.
- No RF hardware calibration, BER/PER validation expansion, or flight qualification is implied by this power-node replacement.

## Acceptance gates

- Real public upstream Basilisk 2.11.0 runtime, not mocks.
- Native `SimpleAntenna.antennaOutMsg -> AntennaPower.antennaSetStateInMsg` wiring.
- PDU-disabled and no-downlink cases produce zero electrical draw.
- Actual downlink with positive delivered rate and PDU enabled produces the expected TX-mode draw (base overhead + `P_Tx`).
- A/B tests prove the native battery net power and thermal Comm input respond to downlink activation.
- RF link budget, CNR, BER/PER and delivered data remain identical between power-node-off and power-node-on runs (no physics change in the comm link).
- Whole-spacecraft engineering-proxy count decreases from 3 to 2; propulsion-enabled count decreases from 4 to 3.
- Run Bundle telemetry and manifest record the official module and message connections.

## Upstream Basilisk 2.11.0 references

- `src/simulation/power/antennaPower/antennaPower.cpp`
- `src/simulation/power/antennaPower/_UnitTest/test_unitAntennaPower.py`
- `src/simulation/communication/downlinkHandling/downlinkHandling.h`
- `src/architecture/msgPayloadDefC/DownlinkHandlingMsgPayload.h`
- Release notes identify `simpleAntenna` and `antennaPower` as upstream modules.
