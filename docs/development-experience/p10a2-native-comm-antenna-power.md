# P10-A2 Native Comm/Data antenna power — development experience

Date: 2026-11-19

Status: implementation and same-head acceptance in progress; do not treat focused test results as a whole-project release sign-off.

## What changed

- Replaced the unified-native whole-spacecraft Comm/Data activity power proxy with official Basilisk 2.11.0 `antennaPower.AntennaPower`.
- The physical `SimpleAntenna.antennaOutMsg` continues unchanged to LinkBudget and also feeds `DownlinkAntennaStatusAdapter`. The adapter creates a separate power-only `AntennaLogMsg` copy: TX only when the PDU permits Comm and native `DownlinkHandling.linkActive && attemptedDataRate > 0`; otherwise OFF.
- Official `AntennaPower` consumes the gated copy and accounts only for RF antenna power (`basePowerNeed=0)); its `P_Tx` is copied from the real antenna message.
- Non-RF base overhead is a separate official PDU/TX-gated `SimplePowerSink`: 11 W unified-native, 25 W composite. Composite retains the historical 2 W PDU-enabled idle draw through a third official `SimplePowerSink`.
- Composite trace/summary exposes `comm_control_power_w` and `max_comm_control_power_w`; unified trace exposes PDU, TX/idle status, attempted rate and total native Comm load.

## Why

The former `DownlinkActivityPowerBridge` inferred power from a rate-normalized project equation. The native `AntennaPower` module consumes the physical antenna log's RF transmit power. The adapter translates operational state into a power-only antenna log; it does not calculate power or change the original RF link.

## Pitfalls and decisions

1. **Use attempted, not delivered, rate.** A packet can be transmitted and then fail BER/PER checks. The transmitter still consumed RF power.
2. **Keep runtime profiles separate.** Unified-native is 16 W TX / 0 W idle; composite is 30 W TX / 2 W PDU-enabled idle.
3. **Separate RF and base draw.** The official `AntennaPower` node is RF-only (`basePowerNeed=0)); non-RF overhead is a separate official `SimplePowerSink`. This prevents RF transmit power and engineering-assumption base load from being conflated.
4. **Do not change the RF antenna state.** The gated antenna log is only for power accounting. The physical `SimpleAntenna.antennaOutMsg` continues to feed LinkBudget unchanged.
5. **Fail closed on invalid inputs.** Missing/unwritten or non-finite PDU, downlink or antenna inputs must not enable TX or idle loads. Only the expected t=0 initialization barrier is excluded from runtime error accounting; later errors remain failures.
6. **Update contracts with implementation.** Proxy-count assertions, Capability outputs, adapter schema, Run Bundle assertions and CI path filters all need to change together.

## Acceptance still required

- Official upstream Basilisk 2.11.0 focused tests on the exact PR head.
- Unified-native TX=16 W = 5 W AntennaPower + 11 W base sink; idle/no-link/PDU-off=0 W.
- Composite TX=30 W = 5 W AntennaPower + 25 W base sink; PDU-enabled idle=2 W; PDU-off=0 W.
- Paired composite run with RF/base power nodes independently disabled: RF LinkBudget and DownlinkHandling telemetry unchanged, battery net power differs by 30 W during TX, and thermal electronics heat input contains the native RF and base energy contributions.
- Same-head full native whole-spacecraft, component/subsystem, Run Bundle, browser and parameter-surface regression.

## Cross-environment import regression found by CI

The first PR run exposed that the new adapter was defined only inside the Basilisk-present branch, while `unified_native.py` imports it at module load time. The module now exports a `None` placeholder in the no-Basilisk branch, matching the existing optional bridge contract. Native runtime construction still requires official Basilisk; the placeholder is import compatibility only.

## Reset-barrier and scheduler corrections from native CI

- The first full Basilisk run showed that t=0 is a reset/scheduler barrier, not a representative physical sample. The health audit excludes the complete t=0 row; every positive-time sample remains strictly checked.
- Composite uses explicit dependency priorities: RF geometry/link budget (58–56), battery/PDU (50/49), mission gate (45), instrument/storage/DownlinkHandling (40/39/38), status adapter (37), official power nodes (36).
- Exact official-module count assertions were refreshed after the P10-A1B per-wheel migration and P10-A2 native antenna/base nodes.

## Follow-up correction: gated AntennaLogMsg and separate base sink

- Same-head CI showed that the initial inherited status-port integration left the official AntennaPower node at 0 W while the adapter reported TX. The model must be verified by its actual PowerNodeUsageMsg, not inferred from a status flag.
- The final design gates a power-only AntennaLogMsg copy and sets AntennaPower.basePowerNeed to zero. The RF source P_Tx/P_Rx are copied from SimpleAntenna; the original RF message and LinkBudget remain unchanged.
- A separate official SimplePowerSink carries the 11 W / 25 W non-RF TX base draw and is controlled by the adapter's TX status. Composite's 2 W idle node remains separate.
