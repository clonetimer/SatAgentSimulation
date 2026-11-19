# P10-A2 Native Comm/Data antenna power — development experience

Date: 2026-11-19

Status: implementation and same-head acceptance in progress; do not treat focused test results as a whole-project release sign-off.

## What changed

- Replaced the unified-native whole-spacecraft Comm/Data activity power proxy with official Basilisk 2.11.0 `antennaPower.AntennaPower`.
- Connected the existing official `SimpleAntenna.antennaOutMsg` directly to `AntennaPower.antennaSetStateInMsg`; retained the RF `P_Tx` as the RF link-budget value.
- Added `DownlinkAntennaStatusAdapter` as a status-only translation layer. TX is enabled only when the Comm PDU permits operation, `DownlinkHandling.linkActive` is true, and `attemptedDataRate > 0`. A failed packet attempt still consumes TX power even if delivered rate is zero.
- Migrated the active composite whole-spacecraft path too. Its 30 W TX profile is represented as 25 W non-RF base overhead plus the antenna's 5 W RF TX power. The historical 2 W idle draw remains a separate official Basilisk `SimplePowerSink`; PDU-off is 0 W.
- Preserved the unified-native 16 W TX / 0 W idle profile as 11 W base overhead plus 5 W antenna TX.
- Added composite trace and summary fields for `comm_control_power_w` and `max_comm_control_power_w`; added unified trace fields for PDU, TX/idle status, attempted rate and native Comm load.

## Why

The former `DownlinkActivityPowerBridge` inferred power from a rate-normalized project equation. The native `AntennaPower` module instead consumes the official antenna status log and its actual `P_Tx`. The adapter supplies only operational state; it does not duplicate the physical power equation.

## Pitfalls and decisions

1. **Use attempted, not delivered, rate.** A packet can be transmitted and then fail BER/PER checks. The transmitter still consumed electrical power.
2. **Keep runtime profiles separate.** The composite path historically includes 2 W idle draw; unified-native does not. Collapsing both to one profile silently changes the product's EPS behavior.
3. **Do not double-count antenna TX.** `basePowerNeed` is non-RF overhead only. The official `AntennaPower` model adds the RF transmit power from `AntennaLogMsgPayload.P_Tx`.
4. **Fail closed on invalid inputs.** Missing/unwritten or non-finite PDU/downlink inputs must not enable TX or idle loads. Only the expected t=0 initialization `NOT_READY` sample is excluded from runtime error accounting; later errors remain failures.
5. **Keep power accounting downstream of RF.** The power node may change battery and thermal states, but must not feed back into `SimpleAntenna`, `LinkBudget` or `DownlinkHandling`. Paired runs must preserve CNR, BER/PER, attempted and delivered data.
6. **Update contracts with implementation.** Proxy-count assertions, Capability output fields, adapter output schema and CI path filters all need to change together. Removing a bridge without updating these leaves false test failures or stale product metadata.

## Acceptance still required

- Official upstream Basilisk 2.11.0 focused tests on the exact PR head.
- Unified-native active TX at 16 W, idle/no-link at 0 W, and PDU-off at 0 W.
- Composite active TX at 30 W, PDU-enabled idle at 2 W, and PDU-off at 0 W.
- Paired composite run with the TX power node independently disabled: RF LinkBudget and DownlinkHandling outputs unchanged, battery net power differs by 30 W during TX, and thermal electronics heat input contains the native TX energy contribution.
- Same-head full native whole-spacecraft, component/subsystem, Run Bundle, browser and parameter-surface regression.
