# P10-A2 Native Comm/Data antenna power — development experience

Date: 2026-11-19

Status: COMPLETE — PR #193 merged to main as `96ae435088aeeddfcd00323126ba865f7f9effd0` on 2026-11-19. P10-A2 same-head CI and native acceptance passed; this is a scoped physical-coupling slice, not a whole-project release sign-off.

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

## Acceptance evidence — PASS

- Official upstream Basilisk 2.11.0 focused tests on exact PR head `7dbaf073402103f76a5c31ae65a72a613b8dbacd`; focused P10-A suite: 61 passed.
- Unified-native TX=16 W = 5 W AntennaPower + 11 W base sink; idle/no-link/PDU-off=0 W.
- Composite TX=30 W = 5 W AntennaPower + 25 W base sink; PDU-enabled idle=2 W; PDU-off=0 W.
- Paired composite run with RF/base power nodes independently disabled: RF LinkBudget and DownlinkHandling telemetry unchanged, battery net power differs by 30 W during TX, and thermal electronics heat input contains the native RF and base energy contributions.
- Same-head full native whole-spacecraft, component/subsystem, Run Bundle, browser and parameter-surface regression passed. The PR's native Basilisk, P9-H, P9-N3, static, no-Basilisk, parameter-surface, dependency-governance and agent-consistency jobs all completed successfully; the Stable continuous-satellite candidate job was skipped by its configured condition.

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


## Physical antenna state is part of the TX gate

- PDU permission and native DownlinkHandling activity are necessary but not sufficient to create RF power. The adapter also requires the physical SimpleAntenna state to be TX or RXTX.
- The power-only antenna log remains OFF when the real antenna is OFF/RX-only, even if downlink telemetry reports a TX attempt. This prevents a false electrical load when the physical RF device cannot transmit.
- A focused Basilisk regression covers antenna-OFF behavior and verifies TX=0, idle follows PDU permission, and the power-only log remains OFF.


## RW power sampling order correction from the same-head P10-A run

- The composite RW regression compared the current ReactionWheelPower output against the current RW `Omega/u_current` recorder and found a 0.134 W mismatch.
- Root cause: the power node was explicitly scheduled at priority 48, which executes before the spacecraft and RW state effector (priority 0). It therefore consumed the previous dynamics tick while the recorder captured the current tick.
- The power node now uses priority 0 and is inserted after the spacecraft/RW effector but before recorder models. PDU priority 49 still executes first, so the power node receives the current PDU permission and current-tick wheel state. Do not force a high priority merely to place a node "after PDU"; verify producer-consumer order for every input.
- The composite RF active A/B test no longer assumes a naturally occurring idle interval. A separate test switches the physical SimpleAntenna OFF, lets the real RF chain produce no downlink attempt, and verifies PDU-on idle draw=2 W, TX/base=0 W, and PDU-off remains covered by the existing low-SOC test.


## Health-audit and battery feedback timing correction

- The first full native run exposed a module-scope `NameError`: the Comm health helper converted adapter nanoseconds with `macros.NANO2SEC` even though `macros` is imported locally inside the runtime builder. Use the exact SI conversion constant `1e-9 s/ns` in the pure audit helper so it remains independent of Basilisk module globals.
- The composite battery is intentionally scheduled before PDU and Comm power nodes to break the battery/PDU/power feedback cycle. Therefore the battery's recorded bus balance at sample `i+1` reflects the power-node output published at sample `i`. The RF A/B test now compares the 30 W battery difference one feedback tick after the TX power-node sample; it still checks same-tick RF and native power-node telemetry separately.


## Final same-head count and terminal-sample corrections

- The project-native inventory increased by one because the new status-only Comm adapter is a real project-owned Basilisk module. The verified whole-spacecraft counts are now 28 project-native modules with propulsion enabled and 27 without propulsion; official module counts remain 29 and 27 respectively.
- A final TX sample at the stop time has no subsequent battery recorder sample in which to observe the one-tick load feedback. The A/B test now skips only that terminal sample and requires at least one valid following battery sample; it does not weaken the 30 W comparison.


## Merge and scope

- PR #193 was squash-merged into `main` as `96ae435088aeeddfcd00323126ba865f7f9effd0` after verifying the exact head SHA and all required same-head jobs. No open PRs remained immediately after merge.
- This acceptance does not claim calibrated RF PA efficiency, rate-dependent DC draw, hardware correlation, flight qualification, or overall Stable release qualification.
