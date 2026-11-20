# P10-A7 whole-spacecraft resource closure implementation plan

> For agentic workers: execute the owned independent tasks below, then integrate
> and request one whole-branch review. Each behavior fix requires observed
> RED-to-GREEN native evidence. Record phase findings in the development log.

**Goal:** Eliminate the two selectable electrical reconstruction proxies and make
whole-spacecraft resource inventories and closure evidence truthful.
**Architecture:** Actual component electrical authorities feed battery and routed
thermal consumers; shared source inventory and native evidence checks describe
the resulting graph. Dynamics-cadence integration preserves short heat pulses.
**Tech stack:** Python, official Basilisk2.11.0, pytest, existing GitHub Actions.
**Spec:** `docs/superpowers/specs/2026-11-20-p10a7-whole-resource-closure-design.md`.

## Global constraints

- No altered thrust, orbit, fuel, data or native battery/PDU physical scheduling.
- Comm compatibility endpoints32/2/0W; fixed fractional-activity profile disclosed.
- Reduced heater efficiency remains1; actual scale and PDU affect heat and draw.
- Raw battery records remain intact; no synthetic initial native state or Reset fix.
- No relaxed conservation limits, hidden disabled tests or false all-official label.

## Review focus

- Permitted idle versus active negative transmitter rate and malformed input.
- Forced heater faults, PDU-off, scaled heat and unconfigured heater targets.
- Coarse/nonharmonic public samples must not erase native depletion or power pulses.
- t=0 invalid payload versus true zero-energy positive-capacity battery evidence.
- Inventory completeness, legacy direct APIs and deliberately disabled couplings.

## Task1: Compatibility electrical authorities and routed heat

**Owned files:** `src/integration/resource_feedback.py`,
`src/components/thermal_node/basilisk_scheduled.py`,
`src/subsystems/thermal/builder.py`, `src/whole_spacecraft/builder.py`,
required EPS builder changes, `tests/test_composite_resource_power_branches.py`.

**Interfaces:** `TransmitterActivityStatusAdapter` with active/idle DeviceStatus
outputs and bounded cumulative read diagnostics; component registry exposes
comm power nodes, actual heater power sources and thermal power bridges for both
branches. Reduced heater owners expose `electricalPowerOutMsg`, actual heat power,
and `subscribe_enable`. Thermal attach functions accept optional
`power_sampling_task_name`; use `ThermalPowerBridge.enable_dynamics_sampling` for
external inputs and schedule such bridges before thermal nodes.

- [x] Reproduce32/2/0W endpoints and the heater/routing/unfunded-heat defects.
- [x] Replace redundant power bridges; fix PDU/scale/hardware and heat destinations.
- [x] Remove coupled standalone heat placeholders; support constant solar/PDU.
- [x] Prove native data/dynamics A/B, correct funding and public configuration matrix.

## Task2: Native battery evidence

**Owned files:** new `src/whole_spacecraft/battery_evidence.py`,
`src/whole_spacecraft/runner.py`, `src/whole_spacecraft/schemas.py`, trace-unit map,
`tests/test_composite_battery_evidence.py`.

**Interface:** checked dense native evidence returns first/last source time,
measured energy delta and right-endpoint integral over the same valid window,
SOC extrema, startup exclusion count and errors. Invalid later evidence must not
be silently removed. Reuse `conservation_battery` at dynamics cadence; Task1 owns
the recorder scheduling edit. Add public battery capacity/source time fields.

- [x] Reproduce false cold-start residual and dense true-empty negative control.
- [x] Implement validity/window/source-time checks and public row alignment.
- [x] Verify coarse exports, transient depletion, invalid/stale/missing samples,
      insufficient evidence and unchanged clamp residual failure.

## Task3: Shared authority inventory

**Owned files:** new `src/integration/power_authority_inventory.py`,
`tests/test_power_authority_inventory.py`; capability declaration proposals.

**Interface:** `PowerAuthorityBinding(model, output_attr, role, recorder_names,
thermal_destinations, assumptions)` and `build_power_authority_inventory(runtime,
authorities, status_adapters=(), thermal_bridges=(), scheduled_models=())` produce
JSON-safe unique model/port entries, source categories and separate counts.
Unknown/unregistered electrical providers are visible, never counted as official.

- [x] Pin truthful official/project-physical/proxy classifications and deduplication.
- [x] Cross-check scheduled output providers and actual written message evidence.
- [x] Return capability/lifecycle corrections; parent integrates both runtime hooks.

## Task4: Dynamics-cadence heat accounting and integration

**Owned files:** `src/subsystems/thermal/thermal_network.py`,
`tests/test_thermal_power_sampling.py`; parent integrates runtime/schema hooks,
capability YAML, reports and workflows after owned edits complete.

**Interface:** `ThermalPowerBridge.enable_dynamics_sampling(sim, task_name,
input_count=None)` creates a bounded sampler for external sources. Thermal updates
consume interval energy as average power; expose pending energy and cumulative
input errors. Network attach excludes the heater's already-deposited thermal
message from external dynamics sampling.

- [x] Reproduce a16J short pulse lost at10s thermal cadence.
- [x] Preserve electrical/heat integrals, destination deposition and partial-window
      pending energy without changing native electrical/dynamics scheduling.
- [x] Integrate source inventory and measured evidence into both public runtimes.
- [x] Run matrix/physical/negative tests and independent review.
- Final-head CI and merge are delivery gates recorded on the containing PR;
  this source snapshot does not preclaim their outcome.
- [x] Record precise before/after counts, phase experience and remaining model scope.
